"""Local evidence-graph teaching CLI: immutable projection, never engineering approval."""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import local_review as review
import range_assessment as assessment
import supplemental_intake as intake


VERSION = "evidence-graph-v1"
CONTEXT_VERSION = "evidence-graph-context-v2"
ENGINE_VERSION = "5.26.29"
ROOT = Path(__file__).resolve().parent
BASE_LABELS = ("CaseSnapshot", "Requirement", "Component", "Specification", "DocumentRevision", "Gap")
LABELS = BASE_LABELS + ("Assessment", "ApplicationContext", "FactReviewReceipt")
RELATIONS = {
    "ABOUT_ORIGINAL": ("CaseSnapshot", "Component"),
    "HAS_REQUIREMENT": ("CaseSnapshot", "Requirement"),
    "CONSIDERS": ("CaseSnapshot", "Component"),
    "HAS_SPEC": ("Component", "Specification"),
    "EXTRACTED_FROM": ("Specification", "DocumentRevision"),
    "HAS_GAP": ("CaseSnapshot", "Gap"),
    "BLOCKS": ("Gap", "Requirement"),
    "HAS_ASSESSMENT": ("CaseSnapshot", "Assessment"),
    "CHECKS": ("Assessment", "Requirement"),
    "BASED_ON": ("Assessment", "ApplicationContext"),
    "HAS_FACT_REVIEW": ("Assessment", "FactReviewReceipt"),
    "USES_SPEC": ("Assessment", "Specification"),
}
GAP_RULES = {
    "fact_review_missing": (
        "人工核對候選規格草稿與原文件",
        "取得綁定 facts_sha256 的明確覆核紀錄；不是工程核准。"),
    "application_conditions_missing": (
        "取得設計資料及相應工程確認",
        "取得綁定本案輸入的 BIAS 對應、VCC 接法及接面溫度證據；符合 comparator context 契約。"),
}
CONTEXT_GAP_RULES = {
    **GAP_RULES,
    "facts_not_accepted": ("完成規格事實覆核", "明確接受同版規格；保留覆核者與來源。"),
    "direct_input_to_bias_mapping_not_established": (
        "確認需求輸入與 BIAS 電壓的對應", "直接映射須有設計依據；否或未知不適用此比較器，不得強改為是。"),
    "vcc_supply_missing": ("補上 VCC 接法", "提供可回查且與同版設計一致的接法。"),
    "junction_temperature_missing": ("補上接面溫度範圍", "提供 min/max/degC 與設計依據；不可直接以環境溫度代替。"),
    "junction_temperature_outside_fact_applicability": (
        "確認溫度適用性", "取得涵蓋案件接面溫度的規格或修訂設計；不可默認適用。"),
    "no_applicable_recommended_fact": ("補上適用的建議工作規格", "精確型號與接法須有對應規格；絕對最大值不算。"),
    "ambiguous_recommended_facts": ("釐清競爭規格", "在新來源版本中釐清適用規格；不得挑選較寬範圍。"),
}
def identity(kind, value):
    return kind + ":" + review.fingerprint(value)


def sha256_text(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("sha256_required")
    return value


def flatten_conditions(fact):
    conditions = fact["conditions"]
    if fact["specification_class"] == "recommended_operating" and fact["property"] == "bias_pin_voltage":
        intake.exact_keys(conditions, ("vcc_supply", "junction_temperature"))
        if conditions["vcc_supply"] not in ("internal_vcc_regulator", "vcc_directly_connected_to_bias"):
            raise ValueError("unsupported_conditions")
        temp = conditions["junction_temperature"]
        assessment.interval(temp, "degC")
        return {"vcc_supply": conditions["vcc_supply"], "tj_min": temp["min"],
                "tj_max": temp["max"], "tj_unit": temp["unit"]}
    if fact["specification_class"] == "absolute_maximum" and fact["property"] == "bias_to_agnd_voltage":
        intake.exact_keys(conditions, ("reference_node",))
        if conditions["reference_node"] != "AGND":
            raise ValueError("unsupported_reference")
        return {"reference_node": "AGND"}
    raise ValueError("unsupported_specification")


def project(requirements, facts, saved, context=None):
    """Application mapper; reuses existing source/review/assessment guards, no DB IO."""
    shown = assessment.show(saved, requirements, facts, context)
    req = review.report(requirements)
    if shown["report_currency"] != "current" or req["source_stale"]:
        raise ValueError("current_inputs_required")
    if context is None and (shown["status"] != "unknown" or set(shown["reason_codes"]) != set(GAP_RULES)):
        raise ValueError("only_current_pending_case_supported")
    if context is not None:
        if (context["requirements_sha256"] != saved["bindings"]["requirements"]
                or context["facts_sha256"] != saved["bindings"]["facts"]
                or context["candidate_model"] != facts["candidate"]["model"]):
            raise ValueError("current_exact_context_required")
        if shown["status"] == "unknown" and not set(shown["reason_codes"]).issubset(CONTEXT_GAP_RULES):
            raise ValueError("unsupported_context_gap")
    if (facts["human_fact_review"] != "pending" or facts["engineering_suitability"] != "not_evaluated"
            or facts["candidate"]["full_orderable_part"] is not None):
        raise ValueError("unsupported_fact_review_or_identity")
    source = facts["source"]
    # This slice has a TI source adapter, not inferred cross-vendor entity resolution.
    url = urlsplit(source["url"])
    if url.scheme != "https" or url.netloc != "www.ti.com" or not url.path.startswith("/lit/ds/"):
        raise ValueError("unsupported_source_adapter")
    sha256_text(source["pdf_sha256"])
    for name in ("document_id", "revision", "retrieved_on"):
        review.require_text(source[name])
    originals = req["fields"]["subject_parts"]
    if originals["status"] not in ("accepted", "corrected") or not originals["item"]["value"]:
        raise ValueError("reviewed_original_identity_required")
    bound = saved["bindings"]
    projection_version = VERSION if context is None else CONTEXT_VERSION
    manifest = {"case_id": req["case_id"], "requirements": bound["requirements"],
                "facts": bound["facts"], "assessment": review.fingerprint(saved),
                "projection_version": projection_version, "rule_version": saved["rule_version"]}
    if context is not None:
        manifest["context"] = bound["context"]
    case_id = identity("case", manifest)
    candidate_id = identity("component", ["TI", facts["candidate"]["model"], "product_model"])
    requirement_id = identity("requirement", [case_id, "input_voltage"])
    document_id = identity("document", ["TI", source["document_id"], source["revision"], source["pdf_sha256"]])
    origin = "synthetic_fixture" if requirements["draft_origin_declared"] == "demo" else "public_case_ai_adaptation"
    if context is not None and context["origin"] == "synthetic_fixture":
        origin = "synthetic_fixture"
    nodes = {label: [] for label in LABELS}
    edges = {relation: [] for relation in RELATIONS}

    def edge(kind, start, end, **properties):
        edges[kind].append({"start": start, "end": end, "properties": properties})

    nodes["CaseSnapshot"].append({"id": case_id, "case_id": req["case_id"],
        "requirements_sha256": bound["requirements"], "assessment_sha256": manifest["assessment"],
        "projection_version": projection_version, "origin": origin, "gap_check_status": "evaluated_voltage_only",
        "reviewer_identity": "self_declared_not_authenticated", "overall": "needs_engineering_review"})
    low, high = req["fields"]["input_min"], req["fields"]["input_max"]
    nodes["Requirement"].append({"id": requirement_id, "property": "board_input_voltage",
        "min": low["item"]["value"], "max": high["item"]["value"], "unit": "V",
        "review_status": "reviewed_extraction", "min_review_status": low["status"], "max_review_status": high["status"],
        "source_ref": "requirements:" + bound["requirements"],
        "min_evidence_ids": low["item"]["evidence_ids"], "max_evidence_ids": high["item"]["evidence_ids"]})
    edge("HAS_REQUIREMENT", case_id, requirement_id)
    nodes["Component"].append({"id": candidate_id, "manufacturer": "TI", "model": facts["candidate"]["model"],
                               "identity_scope": "product_model"})
    edge("CONSIDERS", case_id, candidate_id, facts_sha256=bound["facts"],
         basis_ref="facts:" + bound["facts"] + "#/candidate/selection_basis")
    for model in originals["item"]["value"]:
        # Unknown manufacturer stays source-scoped rather than guessed or globally merged.
        original_id = identity("component", ["source_scoped_original", bound["requirements"], model])
        nodes["Component"].append({"id": original_id, "model": model, "manufacturer_status": "not_resolved",
                                   "identity_scope": "source_scoped_original"})
        edge("ABOUT_ORIGINAL", case_id, original_id)
    nodes["DocumentRevision"].append({"id": document_id, **{k: source[k] for k in
        ("document_id", "revision", "pdf_sha256", "url", "retrieved_on")}})
    for fact in facts["facts"]:
        if fact.get("case_condition_verified", False) is not False:
            raise ValueError("no_verified_application_supported")
        spec_id = identity("specification", [bound["facts"], fact["fact_id"], candidate_id])
        nodes["Specification"].append({"id": spec_id, "fact_id": fact["fact_id"], "facts_sha256": bound["facts"],
            "property": fact["property"], "specification_class": fact["specification_class"],
            **fact["range"], **flatten_conditions(fact), **fact["locator"],
            "review_status": "pending", "conditions_status": "captured", "origin": "source_extraction_draft"})
        edge("HAS_SPEC", candidate_id, spec_id)
        edge("EXTRACTED_FROM", spec_id, document_id)
    gap_rules = GAP_RULES if context is None else CONTEXT_GAP_RULES
    # A range violation is a known result, not missing evidence.
    gap_codes = saved["reason_codes"] if saved["status"] == "unknown" else []
    for code in sorted(gap_codes):
        gap_id = identity("gap", [case_id, candidate_id, saved["assessment_scope"], code])
        action, criterion = gap_rules[code]
        nodes["Gap"].append({"id": gap_id, "candidate_id": candidate_id, "code": code, "status": "open",
            "check_scope": saved["assessment_scope"], "suggested_action": action, "completion_criterion": criterion,
            "source_ref": "assessment:" + manifest["assessment"], "rule_version": projection_version})
        edge("HAS_GAP", case_id, gap_id)
        edge("BLOCKS", gap_id, requirement_id)
    if context is not None:
        assessment_id = identity("assessment", [case_id, manifest["assessment"]])
        common = {"context_sha256": bound["context"], "requirements_sha256": bound["requirements"],
                  "facts_sha256": bound["facts"], "origin": origin,
                  "reviewer_identity": "self_declared_not_authenticated"}
        nodes["Assessment"].append({"id": assessment_id, **common, "status": saved["status"],
            "reason_codes": saved["reason_codes"], "assessment_sha256": manifest["assessment"],
            "rule_version": saved["rule_version"], "scope": saved["assessment_scope"],
            "overall": saved["overall"], "automatic_transition_allowed": False,
            "not_evaluated": saved["not_evaluated"], "selected_fact_id": saved["selected_fact_id"],
            "context_origin_declared": context["origin"]})
        edge("HAS_ASSESSMENT", case_id, assessment_id)
        edge("CHECKS", assessment_id, requirement_id)
        app = context["application"]
        if app is not None:
            app_id = identity("application", [case_id, bound["context"]])
            temp = app["junction_temperature"]
            nodes["ApplicationContext"].append({"id": app_id, **common,
                "mapping_status": "unknown" if app["input_maps_to_bias"] is None else "declared",
                "input_maps_to_bias": app["input_maps_to_bias"], "vcc_supply": app["vcc_supply"],
                "vcc_supply_status": "missing" if app["vcc_supply"] is None else "declared",
                "temperature_status": "missing" if temp is None else "declared",
                "tj_min": None if temp is None else temp["min"], "tj_max": None if temp is None else temp["max"],
                "tj_unit": None if temp is None else temp["unit"],
                "reviewer": app["reviewer"], "reference": app["reference"], "evidence_status": "declared_not_authenticated"})
            edge("BASED_ON", assessment_id, app_id)
        receipt = context["fact_review"]
        if receipt is not None:
            receipt_id = identity("fact-review", [case_id, bound["context"]])
            nodes["FactReviewReceipt"].append({"id": receipt_id, **common, **receipt,
                                               "scope": "fact_extraction_only_not_engineering_approval"})
            edge("HAS_FACT_REVIEW", assessment_id, receipt_id)
        selected = saved["selected_fact_id"]
        if selected is not None:
            spec_id = identity("specification", [bound["facts"], selected, candidate_id])
            edge("USES_SPEC", assessment_id, spec_id)
    return {"manifest": manifest, "snapshot_id": case_id, "document_id": document_id, "nodes": nodes, "edges": edges}


def project_demo(bundle, stage_id):
    """Bundle adapter, not an approval path: legacy missing identity review stays rejected."""
    import interview_demo
    result = interview_demo.inspect_bundle(bundle)
    if result["report_currency"] != "current":
        raise ValueError("current_demo_required")
    stages = [item for item in bundle["stages"] if item["id"] == stage_id]
    if len(stages) != 1:
        raise ValueError("exact_demo_stage_required")
    stage = stages[0]
    return project(bundle["requirements"], bundle["facts"], stage["assessment"], stage["context"])


def import_statement():
    """Only fixed labels/types enter query text. All source content is parameterized."""
    parts = []
    for label in LABELS:
        parts.append(f"CALL {{ UNWIND $nodes.{label} AS row MERGE (n:EvidenceV1:{label} {{id: row.id}}) "
                     f"ON CREATE SET n += row RETURN count(*) AS n_{label} }}")
    for relation, (start_label, end_label) in RELATIONS.items():
        parts.append(f"CALL {{ UNWIND $edges.{relation} AS row "
                     f"MATCH (a:{start_label} {{id: row.start}}), (b:{end_label} {{id: row.end}}) "
                     f"MERGE (a)-[r:{relation}]->(b) ON CREATE SET r += row.properties "
                     f"RETURN count(*) AS e_{relation} }}")
    parts.append("RETURN $snapshot_id AS snapshot_id")
    return "CYPHER 5 " + " ".join(parts)


SPECS = """CYPHER 5
MATCH (c:CaseSnapshot {id: $id})-[cc:CONSIDERS]->(p:Component)
MATCH (p)-[:HAS_SPEC]->(s:Specification)-[:EXTRACTED_FROM]->(d:DocumentRevision)
WHERE s.facts_sha256 = cc.facts_sha256
RETURN p.model AS model, properties(s) AS specification, properties(d) AS document
ORDER BY s.id"""
GAPS = """CYPHER 5
MATCH (c:CaseSnapshot {id: $id})
OPTIONAL MATCH (c)-[:HAS_GAP]->(g:Gap)
RETURN properties(c) AS snapshot, properties(g) AS gap ORDER BY g.id"""
IMPACT = """CYPHER 5
MATCH (d:DocumentRevision {id: $id})<-[:EXTRACTED_FROM]-(s:Specification)
MATCH (s)<-[:HAS_SPEC]-(p:Component)<-[cc:CONSIDERS]-(c:CaseSnapshot)
WHERE s.facts_sha256 = cc.facts_sha256
RETURN DISTINCT c.id AS snapshot_id, c.case_id AS case_id ORDER BY snapshot_id"""
DECISION = """CYPHER 5
MATCH (c:CaseSnapshot {id: $id})-[:HAS_ASSESSMENT]->(a:Assessment)
OPTIONAL MATCH (a)-[:BASED_ON]->(x:ApplicationContext)
OPTIONAL MATCH (a)-[:HAS_FACT_REVIEW]->(r:FactReviewReceipt)
OPTIONAL MATCH (a)-[:USES_SPEC]->(s:Specification)-[:EXTRACTED_FROM]->(d:DocumentRevision)
RETURN properties(a) AS assessment, properties(x) AS application, properties(r) AS fact_review,
       properties(s) AS selected_specification, properties(d) AS selected_document"""
DECISION_IMPACT = """CYPHER 5
MATCH (d:DocumentRevision {id: $id})<-[:EXTRACTED_FROM]-(s:Specification)
MATCH (s)<-[:USES_SPEC]-(a:Assessment)<-[:HAS_ASSESSMENT]-(c:CaseSnapshot)
RETURN DISTINCT c.id AS snapshot_id, c.case_id AS case_id, a.status AS status ORDER BY snapshot_id"""


class GraphError(Exception):
    """Public error categories only; never vendor exception text."""


class Client:
    def __init__(self, password, port=18747):
        if not isinstance(password, str) or not password or type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("local_credentials_required")
        self.port = port
        self.authorization = "Basic " + base64.b64encode(("neo4j:" + password).encode()).decode()

    def query(self, statement, parameters=None):
        body = json.dumps({"statement": statement, "parameters": parameters or {}}, allow_nan=False).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            connection.request("POST", "/db/neo4j/query/v2", body,
                               {"Content-Type": "application/json", "Accept": "application/json",
                                "Authorization": self.authorization})
            response = connection.getresponse()
            data = response.read(1024 * 1024 + 1)
            if response.status not in (200, 202) or len(data) > 1024 * 1024:
                raise GraphError("database_http_error_or_response_limit")
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("errors"):
                raise GraphError("database_query_failed")
            fields, rows = result["data"]["fields"], result["data"]["values"]
            if not isinstance(fields, list) or not isinstance(rows, list) or any(len(row) != len(fields) for row in rows):
                raise GraphError("database_response_invalid")
            return [dict(zip(fields, row)) for row in rows]
        except (OSError, http.client.HTTPException):
            raise GraphError("database_unavailable_outcome_unknown_no_retry") from None
        except (ValueError, KeyError, TypeError):
            raise GraphError("database_response_invalid") from None
        finally:
            connection.close()

    def verify_engine(self):
        rows = self.query("CALL dbms.components() YIELD versions, edition RETURN versions[0] AS version, edition")
        if rows != [{"version": ENGINE_VERSION, "edition": "community"}]:
            raise GraphError("unsupported_database_version_or_edition")

    def initialize(self):
        self.verify_engine()
        foreign = self.query("MATCH (n) WHERE NOT n:EvidenceV1 RETURN count(n) AS count")
        if foreign != [{"count": 0}]:
            raise GraphError("isolated_database_required")
        for label in LABELS:
            self.query(f"CREATE CONSTRAINT bom_v1_{label} IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")

    def import_projection(self, projection):
        self.verify_engine()
        constraints = self.query("SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
                                 "RETURN name, type, labelsOrTypes, properties")
        required_labels = LABELS if projection.get("manifest", {}).get("projection_version") == CONTEXT_VERSION else BASE_LABELS
        for label in required_labels:
            expected = {"name": "bom_v1_" + label, "type": "UNIQUENESS",
                        "labelsOrTypes": [label], "properties": ["id"]}
            if expected not in constraints:
                raise GraphError("initialize_constraints_first")
        return self.query(import_statement(), projection)

    def show(self, snapshot_id):
        gaps = self.query(GAPS, {"id": snapshot_id})
        if not gaps:
            return {"status": "not_found", "snapshot_id": snapshot_id}
        result = {"status": "historical_snapshot", "snapshot": gaps[0]["snapshot"],
                "specifications": self.query(SPECS, {"id": snapshot_id}),
                "gaps": [r["gap"] for r in gaps if r["gap"] is not None],
                "notice": "Historical projection only; no engineering approval or current-source refresh."}
        if result["snapshot"]["projection_version"] == CONTEXT_VERSION:
            decisions = self.query(DECISION, {"id": snapshot_id})
            if len(decisions) != 1:
                raise GraphError("historical_decision_incomplete_or_ambiguous")
            decision = decisions[0]
            checked = decision["assessment"]
            selected = decision["selected_specification"]
            if (checked["status"] in ("matches_requirement", "violates_requirement")
                    and (selected is None or decision["selected_document"] is None
                         or selected["fact_id"] != checked["selected_fact_id"]
                         or selected["facts_sha256"] != checked["facts_sha256"])):
                raise GraphError("historical_decision_dependency_invalid")
            if checked["status"] == "unknown" and selected is not None:
                raise GraphError("unknown_decision_cannot_select_spec")
            result["decision"] = decision
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "initialize", "import", "show", "impact", "decision-impact"))
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--assessment", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--stage")
    parser.add_argument("--id")
    parser.add_argument("--isolated", action="store_true", help="Acknowledge target is dedicated disposable local DB")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        if args.out is not None and args.out.exists():
            raise ValueError("output_exists")
        projection = None
        if args.command in ("plan", "import"):
            if args.bundle is not None:
                if not args.stage or any(x is not None for x in (args.requirements, args.facts, args.assessment, args.context)):
                    raise ValueError("bundle_stage_or_explicit_files_not_both")
                projection = project_demo(intake.read_json(args.bundle), args.stage)
            else:
                if args.stage is not None:
                    raise ValueError("stage_requires_bundle")
                projection = project(
                    intake.read_json(args.requirements or ROOT / "output/requirements-review-20260927/review-11.json"),
                    intake.read_json(args.facts or ROOT / "examples/ti-1222180/candidate-facts-v1.json"),
                    intake.read_json(args.assessment or ROOT / "output/range-assessment-20260927/actual-unknown.json"),
                    None if args.context is None else intake.read_json(args.context))
        if args.command == "plan":
            result = projection
        else:
            if args.command in ("initialize", "import") and not args.isolated:
                raise ValueError("explicit_isolated_target_required")
            if args.command in ("show", "impact", "decision-impact") and not args.id:
                raise ValueError("id_required")
            client = Client(os.environ.get("BOM_NEO4J_PASSWORD"), int(os.environ.get("BOM_NEO4J_PORT", "18747")))
            if args.command == "initialize":
                client.initialize()
                result = {"status": "constraints_initialized", "engine_version": ENGINE_VERSION}
            elif args.command == "import":
                client.import_projection(projection)
                result = client.show(projection["snapshot_id"])
            elif args.command == "show":
                result = client.show(args.id)
            else:
                exact = args.command == "decision-impact"
                result = {"scope": "selected_spec_historical_dependencies" if exact else "possibly_affected_historical_snapshots",
                          "snapshots": client.query(DECISION_IMPACT if exact else IMPACT, {"id": args.id})}
        if args.out is not None:
            review.write_new(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except GraphError as error:
        print(json.dumps({"error": str(error), "notice": "No automatic retry; inspect durable state before resubmitting."}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_input_configuration_or_local_io", "notice": "No approval inferred; no automatic retry."}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
