"""Identity and explicit scope take precedence over retrieval and models."""
from datetime import date
from contracts import QuerySpec, InspectionGroup


def make_query(question, camis=None, inspection_date_key=None, inspection_type=None, borough=None):
    if not question.strip():
        raise ValueError("Question is required")
    if camis is not None:
        if not isinstance(camis, str) or not camis.strip() or camis != camis.strip():
            raise ValueError("CAMIS must be an explicit nonempty string")
        if not inspection_date_key:
            raise ValueError("Select an inspection date with CAMIS")
    elif inspection_date_key or inspection_type:
        raise ValueError("Date/type narrowing requires an explicit CAMIS")
    if inspection_date_key:
        if date.fromisoformat(inspection_date_key).isoformat() != inspection_date_key:
            raise ValueError("Inspection date must be YYYY-MM-DD")
    entity = camis is not None
    return QuerySpec(question=question, camis=camis, inspection_date_key=inspection_date_key,
                     inspection_type=inspection_type, borough=borough,
                     mode="entity" if entity else "conceptual",
                     routing_profile="exact" if entity else "conceptual",
                     lexical_weight=0.7 if entity else 0.3, semantic_weight=0.3 if entity else 0.7,
                     requirements=["identity", "inspection_scope", "findings", "coverage"] if entity else ["findings", "coverage"])


def admission(query: QuerySpec, group: InspectionGroup):
    if query.camis and group.camis and group.camis != query.camis:
        return False, "ENTITY_MISMATCH"
    if not group.camis or not group.inspection_date_key or group.scope_status != "valid":
        return False, "SCOPE_UNKNOWN"
    if query.camis:
        if group.camis != query.camis:
            return False, "ENTITY_MISMATCH"
        if group.inspection_date_key != query.inspection_date_key:
            return False, "DATE_MISMATCH"
        if query.inspection_type is not None and group.inspection_type != query.inspection_type:
            return False, "TYPE_MISMATCH"
    if query.borough:
        boro = getattr(group, "boro", None)
        if boro != query.borough:
            return False, "BOROUGH_MISMATCH"
    return True, "ENTITY_MATCH" if query.camis else "CONCEPTUAL_EXAMPLE"
