"""Pinned company identities for comparisons, without renaming source files.

The caller supplies the authorized, current document-hash catalogue. Only a
sealed, approved profile with source-current aliases may alter logical matching.
Actual DXF tags, BOM row keys, terminal labels and quoted requests stay intact.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy

from .profiles import ManufacturerProfile, ProfileError, resolve_alias, seal_profile


class IdentityError(ValueError):
    pass


class IdentityContext:
    def __init__(self, profile=None, source_catalog=None):
        self.profile = None
        self.catalog = (
            dict(source_catalog) if isinstance(source_catalog, Mapping) else None
        )
        self.groups = {}
        if profile is None:
            return
        try:
            self.profile = seal_profile(
                profile.model_dump(mode="json")
                if isinstance(profile, ManufacturerProfile)
                else profile
            )
        except (ValueError, TypeError, ProfileError) as exc:
            raise IdentityError(
                "The pinned company identity profile is malformed or its version changed."
            ) from exc
        if self.profile.status != "approved" or self.profile.review is None:
            raise IdentityError(
                "The company identity profile is not active and company reviewed."
            )
        approved = [g for g in self.profile.aliases if g.status == "approved"]
        if approved and self.catalog is None:
            raise IdentityError(
                "Approved alias sources require a current owned-document hash catalogue."
            )
        physical = Counter(g.physical_device_id for g in approved)
        for group in approved:
            if physical[group.physical_device_id] != 1:
                raise IdentityError(
                    "A physical identity has multiple approved alias groups."
                )
            for tag in group.tags:
                if tag in self.groups:
                    raise IdentityError(
                        "An exact tag belongs to multiple approved alias groups."
                    )
                self.groups[tag] = group
            for document_id in group.source_ids:
                anchor = next(
                    s for s in self.profile.sources if s.document_id == document_id
                )
                if self.catalog.get(document_id) != anchor.sha256:
                    raise IdentityError(
                        "The approved alias source is stale or unavailable."
                    )

    def canonical(self, tag):
        group = self.groups.get(tag)
        return group.tags[0] if group else tag

    def physical(self, tag):
        group = self.groups.get(tag)
        return group.physical_device_id if group else "tag:" + tag

    def inventory(self, tags, label="inventory"):
        seen = defaultdict(list)
        for tag in tags:
            seen[self.physical(tag)].append(tag)
        if any(len(items) > 1 for items in seen.values()):
            collisions = [", ".join(items) for items in seen.values() if len(items) > 1]
            raise IdentityError(
                f"Distinct {label} entries map to one physical identity: {'; '.join(collisions)}. No entries were merged."
            )
        return {self.canonical(tag): tag for tag in tags}

    def resolve(self, tag, actual_tags):
        tags = list(actual_tags)
        self.inventory(tags, "native device")
        if self.profile is None:
            if tags.count(tag) != 1:
                raise IdentityError(f"No unique actual native device matches {tag}.")
            return tag
        result = resolve_alias(self.profile, tag, tags, source_catalog=self.catalog)
        if result.status != "resolved":
            raise IdentityError(result.reason)
        return result.actual_tag

    def receipt(self):
        if self.profile is None:
            return None
        used = {i for g in self.groups.values() for i in g.source_ids}
        return {
            "profile_id": self.profile.profile_id,
            "profile_version": self.profile.version,
            "tenant_id": self.profile.tenant_id,
            "aliases": [
                {
                    "alias_id": g.alias_id,
                    "physical_device_id": g.physical_device_id,
                    "canonical_tag": g.tags[0],
                    "original_tags": list(g.tags),
                }
                for g in self.profile.aliases
                if g.status == "approved"
            ],
            "sources": [
                s.model_dump(mode="json")
                for s in self.profile.sources
                if s.document_id in used
            ],
        }


def logical_analysis(analysis, context):
    if context.profile is None:
        return analysis
    result = deepcopy(analysis)
    by_source = defaultdict(dict)
    for fact in result["facts"]:
        tag = fact["device_tag"]
        location = fact.get("evidence", {})
        role = fact["role"]
        if role == "bom":
            occurrence = (
                location.get("sheet", ""),
                location.get("row", location.get("cell")),
            )
        elif role == "drawing" and location.get("cad_handle"):
            occurrence = ("native", location["cad_handle"])
        else:
            occurrence = None
        if occurrence is not None:
            by_source[fact["source_id"]][occurrence] = tag
    for inventory in by_source.values():
        context.inventory(inventory.values(), "native device/BOM row")
    for fact in [*result["facts"], *result["requested_changes"]]:
        tag = fact["device_tag"]
        fact.update(
            original_device_tag=tag,
            device_tag=context.canonical(tag),
            physical_device_id=context.physical(tag),
        )
        if tag in context.groups:
            fact["alias_id"] = context.groups[tag].alias_id
    for source in result["sources"]:
        source["original_observed_device_tags"] = source["observed_device_tags"]
        source["observed_device_tags"] = sorted(
            {context.canonical(tag) for tag in source["observed_device_tags"]}
        )
        for uncertain in source.get("uncertain_fields", []):
            uncertain["original_device_tag"] = uncertain["device_tag"]
            uncertain["device_tag"] = context.canonical(uncertain["device_tag"])
    devices = defaultdict(lambda: defaultdict(list))
    for fact in result["facts"]:
        if fact["role"] in {"drawing", "bom"}:
            devices[fact["device_tag"]][fact["field"]].append(fact["id"])
    for device in result["devices"]:
        devices[context.canonical(device["tag"])]
    result["devices"] = [
        {"tag": tag, "fields": dict(fields)} for tag, fields in sorted(devices.items())
    ]
    result["identity_resolution"] = context.receipt()
    return result


def resolve_native_requests(requests, native_tags, profile=None, source_catalog=None):
    """Derive actual selectors anew from original source literals each time."""
    context = IdentityContext(profile, source_catalog)
    original_tags = list(native_tags)
    context.inventory(original_tags, "native device")
    added = {r["device_tag"] for r in requests if r["kind"] == "add_device"}
    context.inventory(
        [*original_tags, *sorted(added)], "existing and explicitly added device"
    )
    result = []
    selector_fields = ("device_tag", "from_terminal", "to_terminal")
    for request in requests:
        resolved = deepcopy(request)
        previous = request.get("original_selectors", {})
        literals = {
            key: previous.get(key, request[key])
            for key in selector_fields
            if key in request
        }
        if previous and context.profile is None:
            raise IdentityError(
                "An alias-resolved native request requires its pinned approved profile again."
            )
        for key, literal in literals.items():
            if key == "device_tag" and request["kind"] == "add_device":
                actual = literal
            elif key.endswith("terminal"):
                tag, pin = literal.rsplit(":", 1)
                actual = (
                    literal
                    if tag in added
                    else context.resolve(tag, original_tags) + ":" + pin
                )
            else:
                actual = context.resolve(literal, original_tags)
            resolved[key] = actual
        if context.profile is not None:
            resolved["original_selectors"] = literals
            resolved["identity_resolution"] = context.receipt()
        result.append(resolved)
    return result
