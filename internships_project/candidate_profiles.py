"""Private browser-side candidate profiles and CV text extraction."""

from __future__ import annotations

import html
from datetime import datetime
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree


MAX_CV_BYTES = 8_000_000
MAX_PROFILE_TEXT = 30_000
MAX_PROFILES = 20


class ProfileError(ValueError):
    """A short, user-facing profile validation error."""


def extract_cv_text(file_path: str | None) -> str:
    if not file_path:
        return ""
    path = Path(file_path)
    if not path.is_file() or path.stat().st_size > MAX_CV_BYTES:
        raise ProfileError("Le CV est introuvable ou dépasse 8 Mo.")
    extension = path.suffix.casefold()
    if extension in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    elif extension == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ProfileError("Installez pypdf pour importer un CV PDF.") from exc
        try:
            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:15])
        except Exception as exc:
            raise ProfileError("Ce PDF n’a pas pu être lu. Essaie un fichier texte.") from exc
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                # DOCX is a ZIP container. Bound both entry count and inflated size so
                # a small compressed archive cannot exhaust memory during extraction.
                if len(entries) > 2_000 or sum(item.file_size for item in entries) > 24_000_000:
                    raise ProfileError("Ce document Word dépasse la taille décompressée autorisée.")
                document = next((item for item in entries if item.filename == "word/document.xml"), None)
                if document is None or document.flag_bits & 0x1:
                    raise ProfileError("Ce document Word est incomplet.")
                if document.file_size > 12_000_000:
                    raise ProfileError("Ce document Word dépasse la taille décompressée autorisée.")
                data = archive.read(document)
            root = ElementTree.fromstring(data)
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n".join(
                "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                for paragraph in root.findall(".//w:p", namespace)
            )
        except (zipfile.BadZipFile, ElementTree.ParseError, OSError) as exc:
            raise ProfileError("Ce document Word n’a pas pu être lu.") from exc
    else:
        raise ProfileError("Formats acceptés : PDF, DOCX, TXT et MD.")
    text = re.sub(r"[ \t]+", " ", text).strip()[:MAX_PROFILE_TEXT]
    if len(text) < 80:
        raise ProfileError("Le CV ne contient pas assez de texte lisible. Ajoute un résumé manuel.")
    return text


def clean_catalog(value: object) -> dict[str, dict]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, record in list(value.items())[:MAX_PROFILES]:
        if isinstance(key, str) and isinstance(record, dict) and isinstance(record.get("name"), str):
            result[key] = record
    return result


def profile_choices(catalog: object) -> list[tuple[str, str]]:
    records = clean_catalog(catalog)
    return sorted(((record["name"], key) for key, record in records.items()),
                  key=lambda item: item[0].casefold())


def save_profile(catalog: object, selected_id: str | None, record: dict) -> tuple[dict, str]:
    records = clean_catalog(catalog)
    if not isinstance(record, dict):
        raise ProfileError("Les informations du profil sont invalides.")
    name = str(record.get("name") or "").strip()[:100]
    if len(name) < 2:
        raise ProfileError("Indique un nom pour ce profil.")
    source_text = str(record.get("source_text") or "").strip()
    if len(source_text) < 80:
        raise ProfileError("Ajoute un CV ou un résumé d’au moins 80 caractères.")
    if len(source_text) > MAX_PROFILE_TEXT:
        raise ProfileError("Le CV ou résumé dépasse la taille maximale autorisée.")
    for field, maximum in (("overview", 2_000), ("start_window", 200)):
        if len(str(record.get(field) or "")) > maximum:
            raise ProfileError("Un des champs du profil est trop long.")
    for field in ("experience", "strengths", "target_roles", "locations", "contracts", "constraints"):
        values = record.get(field, [])
        if isinstance(values, str):
            values = [part.strip() for part in values.replace(",", "\n").splitlines() if part.strip()]
        if not isinstance(values, (list, tuple)) or len(values) > 20 or any(
            not isinstance(item, str) or len(item.strip()) > 300 for item in values
        ):
            raise ProfileError("Un des champs du profil contient trop d’éléments ou un format invalide.")
        record[field] = [item.strip() for item in values if item.strip()]
    for field in ("start_month", "end_month"):
        month = str(record.get(field) or "").strip()
        if month and not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
            raise ProfileError("Saisissez les mois au format AAAA-MM.")
        record[field] = month
    if record.get("start_month") and record.get("end_month") and record["end_month"] < record["start_month"]:
        raise ProfileError("Le mois de fin doit suivre le mois de début.")
    for key, previous in records.items():
        if previous["name"].casefold() == name.casefold() and key != selected_id:
            raise ProfileError("Un profil porte déjà ce nom. Sélectionne-le pour le modifier.")
    key = selected_id if selected_id in records else uuid.uuid4().hex
    if key not in records and len(records) >= MAX_PROFILES:
        raise ProfileError("Le nombre maximal de profils est atteint.")
    records[key] = {**record, "name": name, "source_text": source_text}
    return records, key


def selected_profile(catalog: object, key: str | None) -> dict | None:
    return clean_catalog(catalog).get(key or "")


def profile_prompt(record: dict) -> tuple[str, str, str]:
    name = str(record.get("name") or "").strip()
    facts = [str(record.get("overview") or "").strip()]
    for field, label in (("experience", "Expériences vérifiées"), ("strengths", "Compétences et atouts"),
                         ("constraints", "Réserves et points à confirmer")):
        values = record.get(field)
        if isinstance(values, list) and values:
            facts.append(label + " : " + "; ".join(str(item) for item in values[:12]))
    facts.append("CV ou résumé source :\n" + str(record.get("source_text") or "")[:20_000])
    goals = []
    for field, label in (("target_roles", "Métiers"), ("locations", "Lieux"),
                         ("contracts", "Contrats")):
        values = record.get(field)
        if isinstance(values, list) and values:
            goals.append(label + " : " + ", ".join(str(item) for item in values[:8]))
    if record.get("start_window"):
        goals.append("Période de début : " + str(record["start_window"]))
    return name, "\n\n".join(part for part in facts if part), "; ".join(goals)


def render_profile_card(record: dict | None) -> str:
    if not record:
        return '<div class="profile-empty">Crée ton profil dans Mon profil pour commencer.</div>'
    icons = {
        "Lieux": '<path d="M12 21s7-5.2 7-11a7 7 0 1 0-14 0c0 5.8 7 11 7 11Z"/><circle cx="12" cy="10" r="2.5"/>',
        "Contrat": '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18"/>',
        "Disponibilité": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/>',
    }

    def row(label: str, value: object) -> str:
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        value = str(value or "").strip()
        icon = f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">{icons[label]}</svg>'
        return (f'<div class="profile-summary-fact">{icon}<div class="profile-summary-fact-copy">'
                f'<span>{html.escape(label)}</span><span class="profile-summary-value">{html.escape(value or "À préciser")}</span>'
                '</div></div>')
    def availability() -> str:
        first, last = str(record.get("start_month") or ""), str(record.get("end_month") or "")
        months = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                  "septembre", "octobre", "novembre", "décembre")
        try:
            start = datetime.strptime(first, "%Y-%m") if first else None
            end = datetime.strptime(last, "%Y-%m") if last else None
        except ValueError:
            start = end = None
        if start and end:
            left = months[start.month - 1].capitalize()
            right = months[end.month - 1]
            return f"{left} à {right} {end.year}" if start.year == end.year else f"{left} {start.year} à {right} {end.year}"
        if start or end:
            value = start or end
            return f"{months[value.month - 1].capitalize()} {value.year}"
        return str(record.get("start_window") or "")
    roles = record.get("target_roles")
    if isinstance(roles, str):
        roles = [part.strip() for part in roles.split(",")]
    roles = [str(role).strip() for role in (roles or []) if str(role).strip()]
    chips = "".join(f'<span class="profile-role-chip">{html.escape(role)}</span>' for role in roles)
    if not chips:
        chips = '<span class="profile-role-empty">À préciser</span>'
    return (
        '<section class="profile-summary-card">'
        f'<div class="profile-summary-head"><span class="profile-avatar">{html.escape("".join(part[0] for part in record["name"].split()[:2]).upper())}</span>'
        f'<div><h3>{html.escape(record["name"])}</h3></div></div>'
        '<div class="profile-summary-roles"><span class="profile-summary-label">Métiers</span>'
        f'<div class="profile-role-list">{chips}</div></div>'
        '<div class="profile-summary-grid">'
        + row("Lieux", record.get("locations"))
        + row("Contrat", record.get("contracts"))
        + row("Disponibilité", availability())
        + '</div></section>'
    )

