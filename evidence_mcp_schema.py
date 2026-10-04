"""Versioned wire schemas for the optional MCP delivery boundary, not a truth oracle."""

from evidence_queries import ID_PATTERN, NOTICE


TEXT = {"type": "string", "minLength": 1}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$", "minLength": 64, "maxLength": 64}
NUMBER = {"type": "number"}
STRINGS = {"type": "array", "items": TEXT}
ORIGIN = {"enum": ["synthetic_fixture", "public_case_ai_adaptation"]}
STATUS = {"enum": ["unknown", "matches_requirement", "violates_requirement"]}
PROJECTION = {"enum": ["evidence-graph-v1", "evidence-graph-context-v2"]}
FALSE = {"const": False}
NULL = {"type": "null"}
INPUT_SCHEMA = {"type": "object", "properties": {
    "snapshot_id": {"type": "string", "pattern": "^" + ID_PATTERN + "$", "minLength": 69, "maxLength": 69}},
    "required": ["snapshot_id"], "additionalProperties": False}


def object_schema(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def node_id(kind):
    prefix = kind + ":"
    return {"type": "string", "pattern": "^" + prefix + "[0-9a-f]{64}$",
            "minLength": len(prefix) + 64, "maxLength": len(prefix) + 64}


def nullable(schema):
    return {"anyOf": [NULL, schema]}


SNAPSHOT = object_schema({
    "id": node_id("case"), "case_id": TEXT, "requirements_sha256": HASH, "assessment_sha256": HASH,
    "projection_version": PROJECTION, "origin": ORIGIN, "gap_check_status": {"const": "evaluated_voltage_only"},
    "reviewer_identity": {"const": "self_declared_not_authenticated"},
    "overall": {"const": "needs_engineering_review"}})
DOCUMENT = object_schema({
    "id": node_id("document"), "document_id": TEXT, "revision": TEXT,
    "pdf_sha256": HASH, "url": TEXT, "retrieved_on": TEXT})
SPECIFICATION = object_schema({
    "id": node_id("specification"), "fact_id": TEXT, "facts_sha256": HASH,
    "property": {"enum": ["bias_pin_voltage", "bias_to_agnd_voltage"]},
    "specification_class": {"enum": ["recommended_operating", "absolute_maximum"]},
    "min": NUMBER, "max": NUMBER, "unit": {"const": "V"},
    "vcc_supply": {"enum": ["internal_vcc_regulator", "vcc_directly_connected_to_bias"]},
    "tj_min": NUMBER, "tj_max": NUMBER, "tj_unit": {"const": "degC"}, "reference_node": {"const": "AGND"},
    "page": {"type": "integer", "minimum": 1}, "section": TEXT, "row": TEXT, "footnote": TEXT,
    "review_status": {"const": "pending"}, "conditions_status": {"const": "captured"},
    "origin": {"const": "source_extraction_draft"}},
    ["id", "fact_id", "facts_sha256", "property", "specification_class", "min", "max", "unit",
     "page", "section", "row", "footnote", "review_status", "conditions_status", "origin"])
SPECIFICATION["oneOf"] = [
    {"properties": {"specification_class": {"const": "recommended_operating"},
                    "property": {"const": "bias_pin_voltage"}},
     "required": ["vcc_supply", "tj_min", "tj_max", "tj_unit"], "not": {"required": ["reference_node"]}},
    {"properties": {"specification_class": {"const": "absolute_maximum"},
                    "property": {"const": "bias_to_agnd_voltage"}},
     "required": ["reference_node"],
     "not": {"anyOf": [{"required": [key]} for key in ("vcc_supply", "tj_min", "tj_max", "tj_unit")]}}]
GAP = object_schema({
    "id": node_id("gap"), "candidate_id": node_id("component"), "code": TEXT, "status": {"const": "open"},
    "check_scope": TEXT, "suggested_action": TEXT, "completion_criterion": TEXT,
    "source_ref": TEXT, "rule_version": PROJECTION})
COMMON_CONTEXT = {"context_sha256": HASH, "requirements_sha256": HASH, "facts_sha256": HASH,
                  "origin": ORIGIN, "reviewer_identity": {"const": "self_declared_not_authenticated"}}
ASSESSMENT = object_schema({
    "id": node_id("assessment"), **COMMON_CONTEXT, "status": STATUS, "reason_codes": STRINGS,
    "assessment_sha256": HASH, "rule_version": TEXT, "scope": TEXT,
    "overall": {"const": "needs_engineering_review"}, "automatic_transition_allowed": FALSE,
    "not_evaluated": STRINGS, "selected_fact_id": TEXT,
    "context_origin_declared": {"enum": ["synthetic_fixture", "user_declared"]}},
    ["id", *COMMON_CONTEXT, "status", "reason_codes", "assessment_sha256", "rule_version", "scope",
     "overall", "automatic_transition_allowed", "not_evaluated", "context_origin_declared"])
APPLICATION = object_schema({
    "id": node_id("application"), **COMMON_CONTEXT, "mapping_status": {"enum": ["unknown", "declared"]},
    "input_maps_to_bias": {"type": "boolean"},
    "vcc_supply": TEXT,
    "vcc_supply_status": {"enum": ["missing", "declared"]},
    "temperature_status": {"enum": ["missing", "declared"]},
    "tj_min": NUMBER, "tj_max": NUMBER, "tj_unit": {"const": "degC"},
    "reviewer": TEXT, "reference": TEXT, "evidence_status": {"const": "declared_not_authenticated"}},
    ["id", *COMMON_CONTEXT, "mapping_status", "vcc_supply_status", "temperature_status",
     "reviewer", "reference", "evidence_status"])
# Neo4j properties(n) omits null fields; status declares whether values must exist.
APPLICATION["allOf"] = [
    {"if": {"properties": {status: {"const": "declared"}}},
     "then": {"required": keys}, "else": {"not": {"anyOf": [{"required": [key]} for key in keys]}}}
    for status, keys in (("mapping_status", ["input_maps_to_bias"]), ("vcc_supply_status", ["vcc_supply"]),
                         ("temperature_status", ["tj_min", "tj_max", "tj_unit"]))]
FACT_REVIEW = object_schema({
    "id": node_id("fact-review"), **COMMON_CONTEXT, "status": TEXT,
    "reviewer": TEXT, "reference": TEXT, "scope": {"const": "fact_extraction_only_not_engineering_approval"}})
DECISION = object_schema({
    "assessment": ASSESSMENT, "application": nullable(APPLICATION), "fact_review": nullable(FACT_REVIEW),
    "selected_specification": nullable(SPECIFICATION), "selected_document": nullable(DOCUMENT)})
DECISION["oneOf"] = [
    {"properties": {"assessment": {"properties": {"status": {"const": "unknown"}},
                                   "not": {"required": ["selected_fact_id"]}},
                    "selected_specification": NULL, "selected_document": NULL}},
    {"properties": {"assessment": {"properties": {"status": {"enum": ["matches_requirement", "violates_requirement"]}},
                                   "required": ["selected_fact_id"]},
                    "application": APPLICATION, "fact_review": FACT_REVIEW,
                    "selected_specification": SPECIFICATION, "selected_document": DOCUMENT}}]


def output_schema(data):
    schema = object_schema({
        "schema_version": {"const": "evidence-tools-v1"},
        "status": {"enum": ["historical_snapshot", "not_found", "error"]},
        "data": nullable(data), "error": nullable(TEXT), "engineering_approval": FALSE, "notice": {"const": NOTICE}})
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["oneOf"] = [
        {"properties": {"status": {"const": "historical_snapshot"}, "data": data, "error": NULL}},
        {"properties": {"status": {"const": "not_found"}, "data": NULL, "error": NULL}},
        {"properties": {"status": {"const": "error"}, "data": NULL,
                        "error": {"enum": ["invalid_arguments", "evidence_unavailable", "response_limit",
                                           "internal_error", "invalid_result"]}}}]
    return schema


ASSESSMENT_DATA = object_schema({
    "snapshot": SNAPSHOT, "assessment_record_status": {"enum": ["not_recorded", "recorded"]},
    "decision": nullable(DECISION)})
ASSESSMENT_DATA["oneOf"] = [
    {"properties": {"assessment_record_status": {"const": "not_recorded"}, "decision": NULL}},
    {"properties": {"assessment_record_status": {"const": "recorded"}, "decision": DECISION}}]
OUTPUT_SCHEMAS = {
    "get_candidate_specs": output_schema(object_schema({
        "snapshot": SNAPSHOT, "specifications": {"type": "array", "items": object_schema({
            "model": TEXT, "specification": SPECIFICATION, "document": DOCUMENT})}})),
    "get_case_gaps": output_schema(object_schema({
        "snapshot": SNAPSHOT, "gaps": {"type": "array", "items": GAP}, "zero_gaps_means_pass": FALSE,
        "assessment_status": {"enum": ["not_recorded", "unknown", "matches_requirement", "violates_requirement"]}})),
    "get_assessment_evidence": output_schema(ASSESSMENT_DATA),
}
