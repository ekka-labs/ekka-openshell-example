"""A small JSON-schema checker for the vendored contracts: the rules those schemas use, applied to a value.

type object and array, required, additionalProperties false, const, enum, pattern, minItems/maxItems,
minimum/maximum, maxProperties, propertyNames and additionalProperties as a schema, $ref into $defs, and `not` with a
pattern. Each schema's own rules are READ from it, never restated in a test.
"""
import re


def schema_errors(value, schema, root=None, path=""):
    root = root or schema
    errs = []
    if "$ref" in schema:
        return schema_errors(value, root["$defs"][schema["$ref"].split("/")[-1]], root, path)
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: not one of {schema['enum']}")
    if schema.get("type") == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
        errs.append(f"{path}: not an integer")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            errs.append(f"{path}: out of range")
    if schema.get("type") == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return errs + [f"{path}: not an object"]
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}/{k}: required")
        if len(value) > schema.get("maxProperties", 10 ** 9):
            errs.append(f"{path}: too many properties")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if "propertyNames" in schema and not re.search(schema["propertyNames"]["pattern"], k):
                errs.append(f"{path}/{k}: name not allowed")
            if k in props:
                errs += schema_errors(v, props[k], root, f"{path}/{k}")
            elif extra is False:
                errs.append(f"{path}/{k}: not allowed")
            elif isinstance(extra, dict):
                errs += schema_errors(v, extra, root, f"{path}/{k}")
    if schema.get("type") == "array" or "items" in schema:
        if not isinstance(value, list):
            return errs + [f"{path}: not an array"]
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10 ** 9):
            errs.append(f"{path}: {len(value)} items")
        if schema.get("uniqueItems") and len({repr(v) for v in value}) != len(value):
            errs.append(f"{path}: items repeat")
        for i, item in enumerate(value):
            errs += schema_errors(item, schema["items"], root, f"{path}/{i}")
    if "pattern" in schema and not (isinstance(value, str) and re.search(schema["pattern"], value)):
        errs.append(f"{path}: does not match {schema['pattern']}")
    if "not" in schema and "pattern" in schema["not"] and isinstance(value, str) and re.search(schema["not"]["pattern"], value):
        errs.append(f"{path}: matches what it must not")
    return errs
