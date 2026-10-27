"""Standalone Gradio interface for the internships project."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr

from .internships import (
    Opportunity, TRACKING_STATUSES, export_tracking, import_tracking, load_opportunities,
    load_delayed_opportunities,
    mark_opened_offer_applied, normalize_status, opportunities_loading, render_opportunities, render_tracking,
    save_tracking,
)
from .career_agent import (CareerAgentError, candidate_name, export_letter_pdf,
                           generate_briefs, generate_letter, letter_review_flags, load_candidate_profile,
                           rank_candidate_pool, render_briefs, structure_candidate_profile,
                           save_letter_draft, saved_letter, PROFILE_PATH)
from .candidate_profiles import (ProfileError, clean_catalog, extract_cv_text, profile_choices,
                                 profile_prompt, render_profile_card, save_profile, selected_profile)
from .state_store import (hydrate_local_state, local_state_enabled, persist_tracking,
                          shared_mode, load_user_data, save_user_data)
from .accounts import admin_username, delete_registered_account, list_accounts
from .llm_usage import llm_user, generation_slot, GenerationLimitError, render_usage_report
from .private_downloads import register_download

CSS = Path(__file__).with_name('ui_styles.css').read_text(encoding='utf-8')
# New SKEMA emblem published on the school's 2026 brand announcement.
SKEMA_EMBLEM = base64.b64encode(
    (Path(__file__).parent / 'assets' / 'skema_emblem_2026.png').read_bytes()
).decode('ascii')

APP_THEME = gr.themes.Base(primary_hue="purple", secondary_hue="violet", neutral_hue="slate").set(
    body_background_fill="#f7f8fa", body_background_fill_dark="#f7f8fa",
    background_fill_primary="#ffffff", background_fill_primary_dark="#ffffff",
    background_fill_secondary="#f1f3f7", background_fill_secondary_dark="#f1f3f7",
    block_background_fill="#ffffff", block_background_fill_dark="#ffffff",
    block_label_background_fill="#ffffff", block_label_background_fill_dark="#ffffff",
    panel_background_fill="#ffffff", panel_background_fill_dark="#ffffff",
    input_background_fill="#ffffff", input_background_fill_dark="#ffffff",
    input_background_fill_hover="#f8f7fd", input_background_fill_hover_dark="#f8f7fd",
    input_border_color="#dce1e8", input_border_color_dark="#dce1e8",
    border_color_primary="#dfe3e9", border_color_primary_dark="#dfe3e9",
    body_text_color="#17202b", body_text_color_dark="#17202b",
    body_text_color_subdued="#647184", body_text_color_subdued_dark="#647184",
    button_primary_background_fill="#7657d4", button_primary_background_fill_dark="#7657d4",
    button_primary_text_color="#ffffff", button_primary_text_color_dark="#ffffff",
)

OFFER_RETURN_JS = """
const storageKey = 'markets-career-opened-offer-v1';
const toast = element.querySelector('.offer-return-toast');
const title = element.querySelector('.offer-return-title');
const company = element.querySelector('.offer-return-company');
let pending = null;
try { pending = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch (_) {}
function remember() {
  try { sessionStorage.setItem(storageKey, JSON.stringify(pending)); } catch (_) {}
}
function clearPending() {
  pending = null;
  try { sessionStorage.removeItem(storageKey); } catch (_) {}
  toast.hidden = true;
}
function showOnReturn() {
  if (!pending || !pending.away || document.visibilityState !== 'visible') return;
  if (Date.now() - pending.openedAt > 24 * 60 * 60 * 1000) { clearPending(); return; }
  title.textContent = pending.title;
  company.textContent = pending.company;
  toast.hidden = false;
}
document.addEventListener('click', (event) => {
  const link = event.target.closest('a.job-card-main, a.internship-open');
  const card = link?.closest('.internship-card[data-offer-url]');
  if (!card) return;
  const status = card.dataset.trackingStatus;
  if (status && !['À examiner', 'À candidater'].includes(status)) return;
  pending = {url: card.dataset.offerUrl, title: card.dataset.offerTitle,
             company: card.dataset.offerCompany, openedAt: Date.now(), away: false};
  remember();
});
window.addEventListener('blur', () => { if (pending) { pending.away = true; remember(); } });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden' && pending) { pending.away = true; remember(); }
  if (document.visibilityState === 'visible') setTimeout(showOnReturn, 150);
});
window.addEventListener('focus', () => setTimeout(showOnReturn, 150));
element.addEventListener('click', (event) => {
  if (event.target.closest('[data-offer-return-later]')) { clearPending(); return; }
  if (event.target.closest('[data-offer-return-apply]') && pending) {
    const url = pending.url;
    clearPending();
    trigger('click', {url});
  }
});
showOnReturn();
"""


def build_internships_app() -> gr.Blocks:
    shared = shared_mode()

    def cleared_pdf():
        return gr.HTML("") if shared else gr.File(value=None, visible=False)
    configured = list(json.loads(os.getenv("INTERNSHIPS_USERS_JSON", "{}")).items()) if shared else []
    owner = admin_username(configured) if shared else ""

    def username(request: gr.Request) -> str:
        name = getattr(request, "username", None)
        if shared and not name:
            raise gr.Error("Connecte-toi pour accéder à tes données.")
        return str(name or "")

    def account_opportunities(request: gr.Request, force: bool = False):
        live = load_opportunities(force=force)
        if shared and username(request) != owner:
            return load_delayed_opportunities()
        return live

    def persist_personal(kind: str, data: dict, request: gr.Request) -> bool:
        if shared:
            save_user_data(username(request), kind, data)
            return True
        return persist_tracking(data) if kind == "tracking" else False

    def active_profile(catalog: object) -> tuple[str | None, dict | None]:
        """Use one stable profile for this account, including older catalogs."""
        records = clean_catalog(catalog)
        key = next(iter(records), None)
        return key, records.get(key) if key else None

    nav_js = """
const icons = {
  'Offres': '<svg class="career-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="7" width="19" height="14" rx="2.5"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M2.5 13h19M10 12.5v2h4v-2"/></svg>',
  'Assistant IA': '<svg class="career-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8L12 2ZM19 16l.7 1.8L21.5 18.5l-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7L19 16ZM4.5 15l.6 1.4 1.4.6-1.4.6L4.5 19l-.6-1.4-1.4-.6 1.4-.6L4.5 15Z"/></svg>',
  'Mon profil': '<svg class="career-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 21v-2a7.5 7.5 0 0 1 15 0v2H4.5Z"/></svg>',
  'Administration': '<svg class="career-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M7 9h10M7 13h10M7 17h6"/></svg>'
};
function decorateCareerNav() {
  const app = document.querySelector('#internships-app');
  const nav = [...(app?.querySelectorAll('[role="tablist"]') || [])].find(list => {
    const names = [...list.querySelectorAll('[role="tab"]')].map(tab => tab.textContent.trim());
    return names.includes('Offres') && names.includes('Assistant IA') && names.includes('Mon profil');
  });
  if (!nav) return false;
  nav.classList.add('career-main-nav');
  for (const tab of nav.querySelectorAll('[role="tab"]')) {
    const name = tab.textContent.trim();
    if (icons[name] && !tab.querySelector('.career-nav-icon')) {
      tab.dataset.careerTab = name === 'Offres' ? 'jobs' : name === 'Assistant IA' ? 'assistant' : name === 'Administration' ? 'admin' : 'profile';
      tab.insertAdjacentHTML('afterbegin', icons[name]);
    }
  }
  return true;
}
if (!decorateCareerNav()) requestAnimationFrame(decorateCareerNav);
const careerNavObserver = new MutationObserver(decorateCareerNav);
careerNavObserver.observe(document.querySelector('#internships-app') || document.body,
  {childList: true, subtree: true});
"""
    favicon_path = Path(__file__).parent / "assets" / "skema_emblem_2026.png"
    with gr.Blocks(analytics_enabled=False, title='Offres · Marchés financiers') as app_blocks:
        with gr.Column(elem_id="internships-app"):
            legacy_profile = "" if shared else load_candidate_profile()
            legacy_name = candidate_name(legacy_profile) or "Mon profil"
            initial_catalog = ({"legacy": {"name": legacy_name, "source_text": legacy_profile,
                                           "overview": "", "experience": [], "strengths": [],
                                           "target_roles": [], "locations": [], "contracts": [],
                                           "start_month": "", "end_month": "", "start_window": "",
                                           "constraints": []}} if legacy_profile and not shared else {})
            profiles_state = (gr.State({}) if shared else gr.BrowserState(
                default_value=initial_catalog, storage_key="ai-sales-desk-candidate-profiles-v1",
                secret="ai-sales-desk-candidate-profiles-local-v1"))
            if shared:
                old_profiles = gr.BrowserState(default_value={},
                    storage_key="ai-sales-desk-candidate-profiles-v1",
                    secret="ai-sales-desk-candidate-profiles-local-v1")
                old_tracking = gr.BrowserState(default_value={},
                    storage_key="ai-sales-desk-internships-v1",
                    secret="ai-sales-desk-local-tracking-v1")
                old_feedback = gr.BrowserState(default_value={},
                    storage_key="ai-sales-desk-internships-feedback-v1",
                    secret="ai-sales-desk-local-feedback-v1")
                old_drafts = gr.BrowserState(default_value={},
                    storage_key="ai-sales-desk-internships-agent-drafts-v1",
                    secret="ai-sales-desk-agent-drafts-v1")
            offer_return_toast = gr.HTML(
                '<aside class="offer-return-toast" hidden role="status" aria-live="polite">'
                '<span class="offer-return-eyebrow">Offre consultée</span>'
                '<strong class="offer-return-title"></strong>'
                '<span class="offer-return-company"></span>'
                '<p>Tu as postulé à cette offre ?</p>'
                '<div class="offer-return-actions">'
                '<button type="button" data-offer-return-later>Plus tard</button>'
                '<button type="button" data-offer-return-apply>Marquer « Postulé »</button>'
                '</div></aside>',
                js_on_load=OFFER_RETURN_JS, elem_id="offer-return-notification",
            )
            gr.HTML(
                '<div class="career-masthead" aria-label="À propos du projet">'
                '<div class="career-masthead-brand">'
                '<a class="skema-brand" href="https://www.skema.edu/fr/la-marque-skema-business-school" '
                'target="_blank" rel="noopener noreferrer" aria-label="SKEMA Business School — nouvelle identité">'
                f'<span class="skema-emblem"><img src="data:image/png;base64,{SKEMA_EMBLEM}" alt="" /></span>'
                '<span class="skema-wordmark"><strong>SKEMA</strong><small>BUSINESS SCHOOL</small></span></a>'
                '</div>'
                '<div class="career-masthead-identity">'
                '<span class="career-masthead-course">MSc Financial Markets &amp; Investments</span>'
                '<span class="career-masthead-author">Théo Caruana</span>'
                '</div>'
                '</div>',
                elem_id="career-masthead",
            )
            with gr.Tabs(elem_id="primary-navigation", selected="offres") as primary_tabs:
                with gr.Tab("Offres", id="offres"):
                    gr.HTML(
                        '<header class="internship-hero"><div class="hero-copy">'
                        '<h1>Trouve ton prochain <em>stage</em></h1>'
                        '<p>Les offres en finance de marché, réunies au même endroit.</p>'
                        '</div></header>',
                        js_on_load=nav_js,
                    )
                    with gr.Column(elem_id="internship-search-panel"):
                        with gr.Row(elem_id="internship-search-row"):
                            internship_search = gr.Textbox(
                                label="Rechercher", placeholder="Métier, entreprise ou ville…",
                                show_label=False, container=False, scale=7,
                                elem_id="internship-search-input",
                            )
                            internship_refresh = gr.Button("↻  Actualiser les offres", variant="secondary", scale=1,
                                                           elem_id="internship-refresh")
                        current_month = datetime.now(timezone.utc)
                        month_names = ("Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                                       "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre")
                        month_choices = [("Toutes dates", "Toutes dates")] + [
                            (f"{month_names[(current_month.month - 1 + step) % 12]} "
                             f"{current_month.year + (current_month.month - 1 + step) // 12}",
                             f"{current_month.year + (current_month.month - 1 + step) // 12:04d}-"
                             f"{(current_month.month - 1 + step) % 12 + 1:02d}")
                            for step in range(24)
                        ]
                        with gr.Row(elem_id="internship-quick-filters"):
                            with gr.Column(elem_id="internship-contract-field"):
                                gr.HTML('<span class="job-filter-label">Type de contrat</span>')
                                internship_category = gr.Dropdown(
                                    [("Tous les contrats", "Toutes"), ("Stages · Paris", "Stages Paris"),
                                     ("Stages · International", "Stages International"),
                                     ("VIE", "VIE"), ("Graduate", "Graduate")],
                                    value="Toutes", label="Type de contrat", show_label=False,
                                    container=False, elem_id="internship-contract-types",
                                )
                            with gr.Column(elem_id="internship-family-field"):
                                gr.HTML('<span class="job-filter-label">Métier</span>')
                                internship_family = gr.Dropdown(
                                    ["Tous métiers", "Capital Markets", "Sales", "Trading / Structuring", "Asset Management", "Quant / Research", "Risk / Support"],
                                    value="Tous métiers", label="Métier", show_label=False, container=False,
                                    elem_id="internship-family",
                                )
                            with gr.Column(elem_id="internship-period-field"):
                                gr.HTML('<span class="job-filter-label">Date de publication</span>')
                                internship_period = gr.Dropdown(
                                    [("Toutes les dates", "all"), ("Aujourd’hui", "daily"),
                                     ("7 derniers jours", "weekly")],
                                    value="all", label="Date de publication", show_label=False,
                                    container=False, elem_id="internship-period",
                                )
                            with gr.Column(elem_id="internship-start-field"):
                                gr.HTML('<span class="job-filter-label">Début du contrat</span>')
                                internship_start = gr.Dropdown(
                                    month_choices, value="Toutes dates", label="Début à partir de",
                                    show_label=False, container=False, elem_id="internship-start",
                                )
                        with gr.Row(elem_id="internship-active-filters", visible=False) as internship_active_filters:
                            internship_query_chip = gr.Button(visible=False, elem_classes="active-filter-chip")
                            internship_contract_chip = gr.Button(visible=False, elem_classes="active-filter-chip")
                            internship_family_chip = gr.Button(visible=False, elem_classes="active-filter-chip")
                            internship_period_chip = gr.Button(visible=False, elem_classes="active-filter-chip")
                            internship_start_chip = gr.Button(visible=False, elem_classes="active-filter-chip")
                            internship_reset_filters = gr.Button("Réinitialiser", visible=False,
                                                                 elem_id="internship-reset-filters")
                        internship_show_hors_cible = gr.State(True)
                    internship_feedback = (gr.State({}) if shared else gr.BrowserState(
                        default_value={}, storage_key="ai-sales-desk-internships-feedback-v1",
                        secret="ai-sales-desk-local-feedback-v1"))
                    with gr.Tabs(elem_id="jobs-sections"):
                        with gr.Tab("Offres"):
                            with gr.Row(elem_id="internship-controls-row"):
                                with gr.Column(elem_id="internship-sort-field"):
                                    internship_sort = gr.Dropdown(
                                        [("Par pertinence", "pertinence"), ("Plus récentes d’abord", "recent")],
                                        value="pertinence", label="Trier les offres", show_label=False,
                                        container=False, elem_id="internship-sort",
                                    )
                            internship_board = gr.HTML(
                                '<div class="calendar-empty">Ouvre cet onglet pour charger les offres publiques.</div>',
                            )
                        with gr.Tab("Mes candidatures"):
                            internship_tracking = (gr.State({}) if shared else gr.BrowserState(
                                default_value={}, storage_key="ai-sales-desk-internships-v1",
                                secret="ai-sales-desk-local-tracking-v1"))
                            with gr.Row(elem_id="application-entry-row"):
                                tracked_offer = gr.Dropdown(label="Offre", choices=[], filterable=True, scale=5)
                                tracked_status = gr.Dropdown(
                                    list(TRACKING_STATUSES), value=TRACKING_STATUSES[0], label="Étape", scale=2,
                                )
                                tracking_save = gr.Button("Enregistrer", variant="primary", scale=1)
                            with gr.Accordion("Notes et dates (facultatif)", open=False):
                                with gr.Row():
                                    tracked_follow_up = gr.Textbox(label="Prochaine action (AAAA-MM-JJ)", scale=2)
                                    tracked_deadline = gr.Textbox(label="Date limite (AAAA-MM-JJ)", scale=2)
                                with gr.Row():
                                    tracked_applied_at = gr.Textbox(label="Postulé le (AAAA-MM-JJ)", scale=2)
                                    tracked_note = gr.Textbox(label="Note et contact", lines=2, scale=4)
                                tracking_remove = gr.Button("Retirer cette offre du suivi")
                            tracking_message = gr.Markdown(
                                "Suivi enregistré dans ton compte." if shared else
                                "Suivi sauvegardé automatiquement sur cet ordinateur."
                                if local_state_enabled() else "Suivi conservé dans ce navigateur."
                            )
                            tracking_view = gr.HTML('<div class="pipeline-empty">Chargement de ton suivi…</div>')
                            with gr.Accordion("Sauvegarder ou restaurer mon suivi", open=False):
                                gr.Markdown("La sauvegarde locale est automatique. Copie ce JSON pour transférer ton suivi vers un autre ordinateur.")
                                tracking_backup = gr.Textbox(label="Sauvegarde JSON", lines=6)
                                with gr.Row():
                                    tracking_export = gr.Button("Afficher la sauvegarde")
                                    tracking_import = gr.Button("Importer cette sauvegarde")

                with gr.Tab("Assistant IA", id="assistant"):
                    gr.HTML(
                        '<section class="agent-intro"><div>'
                        '<h2>Prépare tes candidatures</h2>'
                        '<p>Analyse les offres et rédige une lettre adaptée à ton profil.</p>'
                        '</div></section>'
                    )
                    with gr.Column(elem_id="agent-start-card"):
                        with gr.Row(elem_id="agent-start-row"):
                            agent_profile_select = gr.Dropdown(
                                choices=profile_choices(initial_catalog), value=active_profile(initial_catalog)[0],
                                visible=False, elem_id="agent-profile-select",
                            )
                            agent_scan = gr.Button("Analyser les offres", variant="primary", interactive=False,
                                                   elem_id="agent-scan")
                        gr.Markdown("Ton profil est envoyé à OpenAI uniquement quand tu lances une génération.",
                                    elem_id="agent-privacy-note")
                    with gr.Column(elem_id="agent-empty-state") as agent_empty_state:
                        gr.HTML('<div class="agent-empty"><strong>Commence avec ton profil</strong>'
                                '<p>Ajoute ton CV et tes préférences pour recevoir des offres adaptées.</p></div>')
                        agent_create_profile = gr.Button("Créer mon profil", variant="primary")
                    agent_profile_card = gr.HTML("")
                    agent_message = gr.Markdown("")
                    agent_briefs = gr.HTML("", elem_id="agent-briefs", js_on_load="""
element.addEventListener('click', (event) => {
  const button = event.target.closest('.agent-prepare-letter');
  if (!button) return;
  trigger('click', {url: button.dataset.offerUrl});
  document.querySelector('#agent-workspace')?.scrollIntoView({behavior: 'smooth', block: 'start'});
});
""")
                    agent_selection = gr.State([])
                    agent_drafts = (gr.State({}) if shared else gr.BrowserState(
                        default_value={}, storage_key="ai-sales-desk-internships-agent-drafts-v1",
                        secret="ai-sales-desk-agent-drafts-v1"))
                    with gr.Column(elem_id="agent-workspace", visible=False) as agent_workspace:
                        gr.HTML('<h3 class="agent-letter-title">Ta lettre de motivation</h3>')
                        with gr.Row(elem_id="agent-letter-row"):
                            with gr.Column(elem_id="agent-offer-field"):
                                gr.HTML('<label class="agent-field-label" for="agent-offer">Offre pour la lettre</label>')
                                agent_offer = gr.Dropdown(label="Offre pour la lettre", show_label=False,
                                                          container=False, choices=[], scale=4,
                                                          elem_id="agent-offer")
                            with gr.Column(elem_id="agent-language-field"):
                                gr.HTML('<span class="agent-field-label">Langue</span>')
                                agent_language = gr.Radio(["Français", "English"], value="Français",
                                                          label="Langue", show_label=False, container=False, scale=2,
                                                          elem_id="agent-letter-language")
                            with gr.Column(elem_id="agent-letter-action"):
                                agent_letter_button = gr.Button("Préparer la lettre", variant="primary", scale=1,
                                                                elem_id="agent-letter-button")
                        gr.HTML('<label class="agent-field-label agent-editor-label" for="agent-letter">Ta lettre — modifiable avant export</label>')
                        agent_letter = gr.Textbox(label="Ta lettre — modifiable avant export", show_label=False,
                                                  container=False, lines=11, elem_id="agent-letter")
                        agent_review = gr.Markdown("", elem_id="agent-review")
                        with gr.Row(elem_id="agent-export-row"):
                            agent_save_draft = gr.Button("Sauvegarder le brouillon", scale=1, elem_id="agent-save-draft")
                            agent_pdf_button = gr.Button("Exporter en PDF", scale=1, interactive=False,
                                                         elem_id="agent-pdf-button")
                        agent_pdf = (gr.HTML("", elem_id="agent-pdf-file") if shared else
                                     gr.File(label="Fichier PDF", interactive=False, visible=False,
                                             elem_id="agent-pdf-file"))

                with gr.Tab("Mon profil", id="profil") as profile_tab:
                    gr.HTML(
                        '<section class="profile-intro">'
                        '<h2>Ton profil</h2>'
                        '<p>Ajoute ton CV et tes préférences pour personnaliser ta recherche.</p>'
                        '</section>'
                    )
                    profile_select = gr.Dropdown(
                        choices=profile_choices(initial_catalog), value=active_profile(initial_catalog)[0],
                        visible=False, elem_id="profile-select",
                    )
                    if shared:
                        with gr.Accordion("Importer mes données de ce navigateur", open=False):
                            gr.Markdown("Si tu utilisais déjà l’application dans ce navigateur, transfère tes profils, candidatures et brouillons vers ton compte connecté. Tes données déjà enregistrées restent prioritaires en cas de conflit.")
                            import_browser_data = gr.Button("Importer dans mon compte")
                            import_browser_message = gr.Markdown("")
                    with gr.Column(elem_id="profile-cv-card", elem_classes=["profile-section-card"]):
                        gr.HTML('<div class="profile-section-heading"><h3>Ton CV</h3>'
                                '<p>Ajoute un CV ou décris ton parcours pour créer ton profil.</p></div>')
                        with gr.Column(elem_classes=["profile-field"]):
                            gr.HTML('<div class="profile-field-label">Nom du profil</div>')
                            profile_name = gr.Textbox(label="Nom du profil", value="", lines=1, max_lines=1,
                                                      placeholder="Prénom Nom", container=False,
                                                      elem_id="profile-name")
                        with gr.Row(elem_id="profile-source-row"):
                            with gr.Column(elem_id="profile-upload-column"):
                                gr.HTML('<div class="profile-upload-caption"><strong>Importer ton CV</strong>'
                                        '<span>PDF, Word ou texte</span></div>')
                                profile_cv = gr.UploadButton(
                                    "Choisir un fichier",
                                    file_types=[".pdf", ".docx", ".txt", ".md"],
                                    type="filepath", size="sm", elem_id="profile-cv-upload",
                                )
                                profile_cv_text = gr.State("")
                            with gr.Column(elem_id="profile-summary-column"):
                                gr.HTML('<div class="profile-field-label">Résumé du parcours</div>')
                                profile_source = gr.Textbox(
                                    label="Résumé du parcours", value="", lines=5, max_lines=5,
                                    container=False,
                                    placeholder="Formation, expériences, compétences, objectifs et disponibilité. L’import du CV peut compléter ce résumé.",
                                )
                        profile_analyze = gr.Button("Analyser mon CV ou mon résumé", variant="primary",
                                                    elem_id="profile-analyze")
                        profile_status = gr.Markdown("Indique ton nom et ajoute un CV ou un résumé.",
                                                     elem_id="profile-status")
                    with gr.Column(elem_id="profile-experience-card", elem_classes=["profile-section-card"]):
                        gr.HTML('<div class="profile-section-heading"><h3>Ton parcours</h3>'
                                '<p>Relis les informations extraites et complète-les si besoin.</p></div>')
                        with gr.Column(elem_classes=["profile-field"]):
                            gr.HTML('<div class="profile-field-label">Résumé professionnel</div>')
                            profile_overview = gr.Textbox(label="Résumé professionnel", lines=3, max_lines=4,
                                                          container=False)
                        with gr.Row(elem_id="profile-experience-row"):
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Tes expériences</div>')
                                profile_experience = gr.Textbox(label="Tes expériences", lines=4, max_lines=5,
                                                                 container=False, placeholder="Une expérience par ligne")
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Tes compétences</div>')
                                profile_strengths = gr.Textbox(label="Tes compétences", lines=4, max_lines=5,
                                                                container=False, placeholder="Une compétence par ligne")
                    with gr.Column(elem_id="profile-search-card", elem_classes=["profile-section-card"]):
                        gr.HTML('<div class="profile-section-heading"><h3>Ta recherche</h3>'
                                '<p>Précise les offres et la période qui t’intéressent.</p></div>')
                        with gr.Row(elem_id="profile-target-row"):
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Métiers recherchés</div>')
                                profile_roles = gr.Textbox(label="Métiers recherchés", placeholder="Sales, Trading, Quant…",
                                                           lines=1, max_lines=1, scale=2, container=False,
                                                           elem_id="profile-roles")
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Lieux souhaités</div>')
                                profile_locations = gr.Textbox(label="Lieux souhaités", placeholder="Paris, Londres…",
                                                               lines=1, max_lines=1, scale=2, container=False,
                                                               elem_id="profile-locations")
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Contrats</div>')
                                profile_contracts = gr.Textbox(label="Contrats", placeholder="Stage, VIE, Graduate",
                                                               lines=1, max_lines=1, scale=1, container=False,
                                                               elem_id="profile-contracts")
                        gr.HTML('<div class="profile-availability-heading"><strong>Disponibilités</strong>'
                                '<span>Indique les mois au plus tôt et au plus tard (AAAA-MM).</span></div>')
                        with gr.Row(elem_id="profile-dates-row"):
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Au plus tôt</div>')
                                profile_start_month = gr.Textbox(label="Au plus tôt", placeholder="AAAA-MM", scale=1,
                                                                 lines=1, max_lines=1, container=False,
                                                                 elem_id="profile-start-month")
                            with gr.Column(elem_classes=["profile-field"]):
                                gr.HTML('<div class="profile-field-label">Au plus tard</div>')
                                profile_end_month = gr.Textbox(label="Au plus tard", placeholder="AAAA-MM", scale=1,
                                                               lines=1, max_lines=1, container=False,
                                                               elem_id="profile-end-month")
                        with gr.Column(elem_classes=["profile-field"]):
                            gr.HTML('<div class="profile-field-label">Précision facultative sur ta disponibilité</div>')
                            profile_start = gr.Textbox(label="Précision facultative sur ta disponibilité",
                                                       placeholder="Ex. disponible dès mai 2027", lines=1, max_lines=1,
                                                       container=False, elem_id="profile-start-window")
                        with gr.Column(elem_classes=["profile-field"]):
                            gr.HTML('<div class="profile-field-label">Informations à compléter</div>')
                            profile_constraints = gr.Textbox(label="Informations à compléter", lines=3, max_lines=4,
                                                             container=False, placeholder="Un point par ligne")
                        profile_save = gr.Button("Enregistrer les modifications", variant="secondary",
                                                 elem_id="profile-save")
                        profile_delete_confirm = gr.Checkbox(
                            label="Je confirme la suppression de mon profil enregistré.", value=False,
                            elem_id="profile-delete-confirm",
                        )
                        profile_delete = gr.Button("Supprimer mon profil", variant="secondary",
                                                   elem_id="profile-delete")
                        profile_save_status = gr.Markdown("", elem_id="profile-save-status")
                    profile_preview = gr.HTML("")

                if shared:
                    with gr.Tab("Administration", id="administration", visible=False) as admin_tab:
                        gr.HTML('<section class="profile-intro"><h2>Administration</h2>'
                                '<p>Comptes créés et consommation de l’assistant IA.</p></section>')
                        admin_refresh = gr.Button("Actualiser le rapport", variant="primary", size="sm", elem_id="admin-refresh")
                        with gr.Column(elem_id="account-admin-panel"):
                            gr.HTML('<div class="account-admin-heading"><h3>Comptes de l’application</h3>'
                                    '<p>Supprimer un compte bloque aussi définitivement son identifiant. Les comptes initiaux se gèrent dans la configuration du serveur.</p></div>')
                            account_admin_list = gr.HTML("")
                            with gr.Row(elem_id="account-delete-row"):
                                account_to_delete = gr.Dropdown(
                                    label="Compte inscrit à supprimer", choices=[],
                                    value=None, elem_id="account-delete-select",
                                )
                                admin_delete = gr.Button("Supprimer et bloquer l’identifiant", variant="secondary",
                                                         elem_id="account-delete-button")
                            admin_delete_confirm = gr.Checkbox(
                                label="Je confirme la suppression de son accès et de ses données, et le blocage définitif de son identifiant.",
                                value=False, elem_id="account-delete-confirm",
                            )
                            admin_delete_message = gr.Markdown("", elem_id="account-delete-message")
                        with gr.Column(elem_id="usage-admin-panel"):
                            gr.HTML('<div class="account-admin-heading"><h3>Coûts de l’API IA</h3>'
                                    '<p>Suivi par compte, par usage et par appel.</p></div>')
                            admin_usage_html = gr.HTML("")

            gr.HTML(
                '<footer class="career-credit">Un projet de <strong>Théo Caruana</strong>'
                '<span aria-hidden="true">·</span> Développé avec Codex '
                '<span aria-hidden="true">·</span> <a href="/confidentialite" target="_blank" rel="noopener">Confidentialité</a></footer>',
                elem_id="career-credit",
            )


        def offer_choices(jobs: list[Opportunity], state: dict | None):
            entries = state if isinstance(state, dict) else {}
            tracked = [
                (f"{normalize_status(entry.get('status'))} · {entry.get('company', '')} · {str(entry.get('title', 'Offre'))[:65]}", url)
                for url, entry in reversed(list(entries.items())[-100:])
                if isinstance(url, str) and url.startswith("https://") and isinstance(entry, dict)
            ]
            saved_urls = {url for _, url in tracked}
            published = [(f"{job.company} · {job.title[:75]}", job.url)
                         for job in jobs if job.url not in saved_urls]
            return tracked + published

        def show_internships(category: str, query: str, family: str, earliest: str,
                             tracking: dict, feedback: dict, show_hors_cible: bool, period: str, sort_by: str,
                             force: bool = False, request: gr.Request = None):
            jobs, errors, updated = account_opportunities(request, force=force)
            return (
                render_opportunities(
                    jobs, errors, updated, category, query, family, earliest, tracking=tracking,
                    hide_dismissed=True,
                    refreshing=opportunities_loading() and (not shared or username(request) == owner),
                    daily=period == "daily", weekly=period == "weekly",
                    feedback=feedback, show_hors_cible=show_hors_cible, sort_by=sort_by,
                    delayed_updates=shared and username(request) != owner,
                ),
                gr.Dropdown(choices=offer_choices(jobs, tracking), value=None),
                render_tracking(tracking, jobs),
            )

        internship_inputs = [
            internship_category, internship_search, internship_family, internship_start, internship_tracking,
            internship_feedback, internship_show_hors_cible,
            internship_period, internship_sort,
        ]
        internship_outputs = [internship_board, tracked_offer, tracking_view]

        filter_controls = [internship_search, internship_category, internship_family,
                           internship_period, internship_start]
        filter_chips = [internship_query_chip, internship_contract_chip, internship_family_chip,
                        internship_period_chip, internship_start_chip]
        chip_outputs = [internship_active_filters, *filter_chips, internship_reset_filters]

        def update_filter_chips(query: str, category: str, family: str, period: str, earliest: str):
            active = [
                (bool(query.strip()), f"Recherche : {query.strip()}  ×"),
                (category != "Toutes", f"{dict([('Toutes', 'Tous les contrats'), ('Stages Paris', 'Stages · Paris'), ('Stages International', 'Stages · International'), ('VIE', 'VIE'), ('Graduate', 'Graduate')]).get(category, category)}  ×"),
                (family != "Tous métiers", f"{family}  ×"),
                (period != "all", f"{dict([('daily', 'Aujourd’hui'), ('weekly', '7 derniers jours')]).get(period, period)}  ×"),
                (earliest != "Toutes dates", f"Début : {next((label for label, value in month_choices if value == earliest), earliest)}  ×"),
            ]
            visible = any(enabled for enabled, _ in active)
            return (gr.update(visible=visible),
                    *(gr.update(value=label, visible=enabled) for enabled, label in active),
                    gr.update(visible=visible))

        for control in filter_controls:
            control.change(update_filter_chips, inputs=filter_controls, outputs=chip_outputs,
                           queue=False, show_progress="hidden")

        defaults = ["", "Toutes", "Tous métiers", "all", "Toutes dates"]
        for chip, control, default in zip(filter_chips, filter_controls, defaults):
            chip.click(lambda value=default: value, outputs=control, queue=False).then(
                show_internships, inputs=internship_inputs, outputs=internship_outputs,
            ).then(update_filter_chips, inputs=filter_controls, outputs=chip_outputs, queue=False)
        internship_reset_filters.click(
            lambda: defaults, outputs=filter_controls, queue=False,
        ).then(show_internships, inputs=internship_inputs, outputs=internship_outputs).then(
            update_filter_chips, inputs=filter_controls, outputs=chip_outputs, queue=False,
        )

        def hydrate_state(tracking: dict, feedback: dict, request: gr.Request):
            if shared:
                account = username(request)
                return (load_user_data(account, "tracking"),
                        load_user_data(account, "feedback"),
                        clean_catalog(load_user_data(account, "profiles")),
                        load_user_data(account, "drafts"))
            try:
                saved_tracking, saved_feedback = hydrate_local_state(tracking, feedback)
            except OSError:
                gr.Warning("Sauvegarde locale inaccessible ; le suivi reste disponible dans ce navigateur.")
                saved_tracking, saved_feedback = tracking or {}, feedback or {}
            return saved_tracking, saved_feedback, gr.skip(), gr.skip()

        hydration_event = app_blocks.load(
            hydrate_state, inputs=[internship_tracking, internship_feedback],
            outputs=[internship_tracking, internship_feedback, profiles_state, agent_drafts],
        ).then(show_internships, inputs=internship_inputs, outputs=internship_outputs)
        if shared:
            def account_overview(request: gr.Request):
                if username(request) != owner:
                    return gr.update(visible=False), "", "", gr.Dropdown(choices=[], value=None)
                entries = list_accounts(configured)
                rows = "".join(
                    '<div class="account-admin-row"><strong>' + html.escape(name)
                    + '</strong><span>' + html.escape(kind) + '</span><time>'
                    + html.escape(created[:10] if created else "Compte initial") + '</time></div>'
                    for name, kind, created in entries
                )
                accounts_html = (
                    f'<div class="account-admin-count">{len(entries)} compte(s)</div>'
                    f'<div class="account-admin-rows">{rows}</div>'
                )
                deletable = [(name, name) for name, kind, _ in entries if kind == "Inscription"]
                return (gr.update(visible=True), accounts_html, render_usage_report(),
                        gr.Dropdown(choices=deletable, value=None))

            app_blocks.load(account_overview, outputs=[admin_tab, account_admin_list,
                                                       admin_usage_html, account_to_delete],
                            queue=False, show_progress="hidden")
            admin_refresh.click(account_overview,
                                outputs=[admin_tab, account_admin_list, admin_usage_html,
                                         account_to_delete], queue=False)

            def remove_account(target: str, confirmed: bool, request: gr.Request):
                if username(request) != owner:
                    raise gr.Error("Seul l’administrateur peut supprimer un compte.")
                if not confirmed:
                    raise gr.Error("Confirme la suppression avant de continuer.")
                try:
                    delete_registered_account(target, configured)
                except ValueError as exc:
                    raise gr.Error(str(exc)) from exc
                return (f"Le compte **{html.escape(target)}** et ses données ont été supprimés ; "
                        "cet identifiant est désormais bloqué pour toute nouvelle inscription."), False

            admin_delete.click(
                remove_account, inputs=[account_to_delete, admin_delete_confirm],
                outputs=[admin_delete_message, admin_delete_confirm],
            ).then(account_overview,
                   outputs=[admin_tab, account_admin_list, admin_usage_html,
                            account_to_delete])
        internship_category.change(
            show_internships, inputs=internship_inputs, outputs=internship_outputs
        )
        internship_search.submit(
            show_internships, inputs=internship_inputs, outputs=internship_outputs
        )
        internship_family.change(
            show_internships, inputs=internship_inputs, outputs=internship_outputs
        )
        internship_start.change(
            show_internships, inputs=internship_inputs, outputs=internship_outputs
        )
        internship_period.change(
            show_internships, inputs=internship_inputs, outputs=internship_outputs
        )
        internship_sort.change(
            show_internships, inputs=internship_inputs, outputs=internship_outputs
        )
        def manual_refresh(category: str, query: str, family: str, earliest: str,
                           tracking: dict, feedback: dict, show_hors_cible: bool,
                           period: str, sort_by: str, request: gr.Request):
            return show_internships(category, query, family, earliest, tracking,
                                    feedback, show_hors_cible, period, sort_by, True, request)

        internship_refresh.click(
            manual_refresh,
            inputs=internship_inputs, outputs=internship_outputs,
        )
        internship_timer = gr.Timer(value=10, active=True)
        def refresh_internship_timer(category: str, query: str, family: str, earliest: str,
                                     tracking: dict, feedback: dict, show_hors_cible: bool,
                                     period: str, sort_by: str, selected: str,
                                     request: gr.Request):
            board, _, tracking_html = show_internships(
                category, query, family, earliest, tracking, feedback, show_hors_cible,
                period, sort_by, request=request,
            )
            jobs, _, _ = account_opportunities(request)
            choices = offer_choices(jobs, tracking)
            urls = {url for _, url in choices}
            return (board,
                    gr.Dropdown(choices=choices, value=selected if selected in urls else None),
                    tracking_html)

        internship_timer.tick(
            refresh_internship_timer, inputs=[*internship_inputs, tracked_offer],
            outputs=internship_outputs,
            queue=False, show_progress="hidden",
        )

        def load_tracking_fields(url: str, tracking: dict, request: gr.Request):
            entry = (tracking or {}).get(url, {}) if isinstance(tracking, dict) else {}
            jobs, _, _ = account_opportunities(request)
            job = next((item for item in jobs if item.url == url), None)
            return (normalize_status(entry.get("status")), entry.get("note", ""),
                    entry.get("deadline") or (job.deadline if job else ""),
                    entry.get("follow_up", ""), entry.get("applied_at", ""))

        tracked_offer.change(
            load_tracking_fields, inputs=[tracked_offer, internship_tracking],
            outputs=[tracked_status, tracked_note, tracked_deadline, tracked_follow_up, tracked_applied_at],
        )

        def update_tracking(url: str, status: str, note: str, deadline: str, follow_up: str,
                            applied_at: str,
                            tracking: dict, request: gr.Request, remove: bool = False):
            jobs, _, _ = account_opportunities(request)
            current = next((job for job in jobs if job.url == url), None)
            state = tracking if isinstance(tracking, dict) else {}

            def response(updated: dict, message: str, selected: str | None):
                return (updated, gr.Dropdown(choices=offer_choices(jobs, updated), value=selected),
                        render_tracking(updated, jobs), message)

            if not url:
                return response(state, "Sélectionne une offre.", None)
            if remove:
                updated = dict(state)
                updated.pop(url, None)
                try:
                    persist_personal("tracking", updated, request)
                except OSError:
                    gr.Warning("Suppression enregistrée dans ce navigateur ; sauvegarde locale inaccessible.")
                return response(updated, "Offre retirée du suivi.", None)
            if current is None:
                entry = state.get(url)
                if not isinstance(entry, dict):
                    return response(state, "Cette offre n'est plus dans les sources. Sélectionne une offre publiée.", None)
                current = Opportunity(
                    title=str(entry.get("title") or "Offre"), company=str(entry.get("company") or ""),
                    location=str(entry.get("location") or ""), kind="Stage", url=url,
                    published="", score=0,
                )
            updated = save_tracking(state, current, status, note, deadline, follow_up, applied_at)
            try:
                saved_locally = persist_personal("tracking", updated, request)
            except OSError:
                saved_locally = False
                gr.Warning("Suivi enregistré dans ce navigateur ; sauvegarde locale inaccessible.")
            message = ("Suivi enregistré dans ton compte." if shared and saved_locally else
                       "Suivi sauvegardé automatiquement sur cet ordinateur." if saved_locally
                       else "Suivi enregistré dans ce navigateur.")
            return response(updated, message, url)

        tracking_inputs = [tracked_offer, tracked_status, tracked_note, tracked_deadline,
                           tracked_follow_up, tracked_applied_at, internship_tracking]
        tracking_outputs = [internship_tracking, tracked_offer, tracking_view, tracking_message]

        def confirm_opened_offer(category: str, query: str, family: str, earliest: str,
                                 tracking: dict, feedback: dict,
                                 show_hors_cible: bool, period: str, sort_by: str,
                                 evt: gr.EventData, request: gr.Request):
            url = str(getattr(evt, "url", "") or "")
            jobs, _, _ = account_opportunities(request)
            updated, changed = mark_opened_offer_applied(tracking, jobs, url)
            if changed:
                try:
                    persist_personal("tracking", updated, request)
                except OSError:
                    gr.Warning("Candidature enregistrée dans ce navigateur ; sauvegarde locale inaccessible.")
                gr.Info("Offre ajoutée à Mes candidatures avec le statut « Postulé ».")
            else:
                gr.Warning("Cette offre est déjà suivie ou n’est plus dans les offres disponibles.")
            board, _, tracking_html = show_internships(
                category, query, family, earliest, updated,
                feedback, show_hors_cible, period, sort_by, request=request,
            )
            choices = offer_choices(jobs, updated)
            selected = url if changed and any(choice_url == url for _, choice_url in choices) else None
            return (updated, board, gr.Dropdown(choices=choices, value=selected),
                    tracking_html, "Candidature ajoutée au statut Postulé." if changed else "Aucune modification du suivi.")

        offer_return_toast.click(
            confirm_opened_offer, inputs=internship_inputs,
            outputs=[internship_tracking, internship_board, tracked_offer, tracking_view, tracking_message],
        )
        tracking_save.click(update_tracking, inputs=tracking_inputs, outputs=tracking_outputs)
        def remove_tracking(url: str, status: str, note: str, deadline: str,
                            follow_up: str, applied_at: str, tracking: dict,
                            request: gr.Request):
            return update_tracking(url, status, note, deadline, follow_up,
                                   applied_at, tracking, request, True)

        tracking_remove.click(
            remove_tracking,
            inputs=tracking_inputs, outputs=tracking_outputs,
        )
        tracking_export.click(export_tracking, inputs=[internship_tracking], outputs=[tracking_backup])

        def restore_tracking(payload: str, state: dict, request: gr.Request):
            updated, message = import_tracking(payload, state)
            if updated != (state or {}):
                try:
                    persist_personal("tracking", updated, request)
                except OSError:
                    gr.Warning("Import visible dans ce navigateur ; sauvegarde locale inaccessible.")
            jobs, _, _ = account_opportunities(request)
            return (updated, gr.Dropdown(choices=offer_choices(jobs, updated), value=None),
                    render_tracking(updated, jobs), message)

        tracking_import.click(
            restore_tracking, inputs=[tracking_backup, internship_tracking],
            outputs=tracking_outputs,
        )

        profile_editor = [profile_name, profile_source, profile_overview, profile_experience,
                          profile_strengths, profile_roles, profile_locations, profile_contracts,
                          profile_start, profile_start_month, profile_end_month, profile_constraints]

        def lines(values: object) -> str:
            return "\n".join(str(item) for item in values) if isinstance(values, list) else ""

        def items(value: str) -> list[str]:
            return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()][:12]

        def editor_values(record: dict | None):
            record = record or {}
            return (record.get("name", ""), record.get("source_text", ""), record.get("overview", ""),
                    lines(record.get("experience")), lines(record.get("strengths")),
                    lines(record.get("target_roles")), lines(record.get("locations")),
                    lines(record.get("contracts")), record.get("start_window", ""),
                    record.get("start_month", ""), record.get("end_month", ""),
                    lines(record.get("constraints")))

        def choose_profile(key: str, catalog: dict):
            record = selected_profile(catalog, key)
            return (*editor_values(record), render_profile_card(record) if record else "",
                    "Profil chargé. Modifie les champs puis enregistre si nécessaire." if record else "Crée un profil pour commencer.", "")

        profile_tab.select(
            lambda catalog: (gr.Dropdown(value=active_profile(catalog)[0]),
                             *editor_values(active_profile(catalog)[1]), None, "",
                             render_profile_card(active_profile(catalog)[1]) if active_profile(catalog)[1] else "",
                             "Modifie ton profil puis enregistre." if active_profile(catalog)[1]
                             else "Indique ton nom et ajoute un CV ou un résumé.", ""),
            inputs=[profiles_state],
            outputs=[profile_select, *profile_editor, profile_cv, profile_cv_text, profile_preview,
                     profile_status, profile_save_status],
        )
        profile_cv.upload(
            lambda file_path: _extract_upload(file_path),
            inputs=[profile_cv], outputs=[profile_status, profile_cv_text],
        )

        def _extract_upload(file_path: str | None):
            if not file_path:
                return "Aucun CV sélectionné.", ""
            try:
                text = extract_cv_text(file_path)
                return f"CV lu et supprimé du stockage temporaire ({Path(file_path).suffix.upper()}). Tu peux lancer l’analyse.", text
            except ProfileError as exc:
                return str(exc), ""
            finally:
                try:
                    Path(file_path).unlink(missing_ok=True)
                except (OSError, TypeError):
                    pass

        def catalog_updates(catalog: dict, key: str):
            choices = profile_choices(catalog)
            return (catalog, gr.Dropdown(choices=choices, value=key),
                    gr.Dropdown(choices=choices, value=key))

        def analyze_profile(key: str, name: str, source: str, cv_text: str,
                            catalog: dict, request: gr.Request):
            try:
                if len(source or "") > 30_000 or len(cv_text or "") > 30_000:
                    raise ProfileError("Le CV et le résumé dépassent 30 000 caractères.")
                combined = "\n\n".join(part for part in (source.strip(), cv_text) if part)
                if len(combined) > 30_000:
                    raise ProfileError("Le CV et le résumé dépassent 30 000 caractères.")
                with llm_user(username(request) if shared else "local"), generation_slot():
                    record = structure_candidate_profile(name, combined)
                updated, selected = save_profile(catalog, active_profile(catalog)[0], record)
                if shared:
                    save_user_data(username(request), "profiles", updated)
            except (ProfileError, CareerAgentError, GenerationLimitError) as exc:
                raise gr.Error(str(exc)) from exc
            return (*catalog_updates(updated, selected), *editor_values(record), None, "",
                    render_profile_card(record),
                    "Profil analysé et enregistré dans ton compte. Relis les informations extraites." if shared
                    else "Profil analysé et enregistré dans ce navigateur. Relis les informations extraites.",
                    render_profile_card(record), "", gr.Button(interactive=True),
                    gr.Column(visible=False))

        profile_analyze.click(
            analyze_profile,
            inputs=[profile_select, profile_name, profile_source, profile_cv_text, profiles_state],
            outputs=[profiles_state, profile_select, agent_profile_select, *profile_editor,
                     profile_cv, profile_cv_text, profile_preview, profile_status, agent_profile_card,
                     profile_save_status, agent_scan, agent_empty_state],
            time_limit=105, show_progress="minimal",
        )

        def save_profile_edits(key: str, catalog: dict, name: str, source: str, overview: str,
                               experience: str, strengths: str, roles: str, locations: str,
                               contracts: str, start: str, start_month: str, end_month: str,
                               constraints: str, request: gr.Request):
            record = {"name": name, "source_text": source, "overview": overview,
                      "experience": items(experience), "strengths": items(strengths),
                      "target_roles": items(roles), "locations": items(locations),
                      "contracts": items(contracts), "start_window": start.strip(),
                      "start_month": start_month.strip(), "end_month": end_month.strip(),
                      "constraints": items(constraints)}
            try:
                updated, selected = save_profile(catalog, active_profile(catalog)[0], record)
                if shared:
                    save_user_data(username(request), "profiles", updated)
            except ProfileError as exc:
                raise gr.Error(str(exc)) from exc
            return (*catalog_updates(updated, selected), render_profile_card(record),
                    "Modifications enregistrées dans ton compte." if shared else
                    "Modifications enregistrées dans ce navigateur.", render_profile_card(record),
                    gr.Button(interactive=True), gr.Column(visible=False))

        profile_save.click(
            save_profile_edits, inputs=[profile_select, profiles_state, *profile_editor],
            outputs=[profiles_state, profile_select, agent_profile_select,
                     profile_preview, profile_save_status, agent_profile_card,
                     agent_scan, agent_empty_state],
        )

        def delete_profile(key: str, catalog: dict, confirmed: bool, request: gr.Request):
            account = username(request) if shared else ""
            if not confirmed:
                raise gr.Error("Coche la confirmation avant de supprimer ton profil.")
            records = clean_catalog(catalog)
            selected = key if key in records else active_profile(records)[0]
            if not selected:
                raise gr.Error("Aucun profil enregistré à supprimer.")
            records.pop(selected, None)
            if shared:
                save_user_data(account, "profiles", records)
            from .private_downloads import remove_user_downloads
            remove_user_downloads(account or "local")
            if not shared:
                PROFILE_PATH.unlink(missing_ok=True)
            save_user_data(account or "local", "ai_cache", {})
            empty = editor_values(None)
            return (records, gr.Dropdown(choices=profile_choices(records), value=None),
                    gr.Dropdown(choices=profile_choices(records), value=None), *empty, None, "",
                    "", "Profil supprimé. Le CV importé avait déjà été effacé après sa lecture.",
                    "", "", gr.Button(interactive=False), gr.Column(visible=True),
                    gr.Button(interactive=False), gr.Dropdown(choices=[], value=None), [],
                    "Ton profil a été supprimé.", "", cleared_pdf(),
                    gr.Button(interactive=False), gr.Column(visible=False))

        profile_delete.click(
            delete_profile,
            inputs=[profile_select, profiles_state, profile_delete_confirm],
            outputs=[profiles_state, profile_select, agent_profile_select, *profile_editor,
                     profile_cv, profile_cv_text, profile_preview, profile_save_status,
                     agent_profile_card, agent_briefs, agent_scan,
                     agent_empty_state, agent_letter_button, agent_offer, agent_selection,
                     agent_message, agent_letter, agent_pdf, agent_pdf_button, agent_workspace],
        )

        def hydrate_profiles(catalog: dict):
            records = clean_catalog(catalog)
            key, record = active_profile(records)
            return (gr.Dropdown(choices=profile_choices(records), value=key),
                    gr.Dropdown(choices=profile_choices(records), value=key),
                    *editor_values(record), render_profile_card(record) if record else "",
                    render_profile_card(record) if record else "",
                    gr.Button(interactive=bool(record)), gr.Column(visible=not bool(record)))

        hydration_event.then(
            hydrate_profiles, inputs=[profiles_state],
            outputs=[profile_select, agent_profile_select, *profile_editor,
                     profile_preview, agent_profile_card, agent_scan, agent_empty_state],
        )

        if shared:
            def migrate_browser_data(previous_profiles: dict, previous_tracking: dict,
                                     previous_feedback: dict, previous_drafts: dict,
                                     request: gr.Request):
                account = username(request)
                values = {
                    "profiles": clean_catalog(previous_profiles),
                    "tracking": import_tracking(export_tracking(previous_tracking), {})[0],
                    "feedback": previous_feedback if isinstance(previous_feedback, dict) else {},
                    "drafts": previous_drafts if isinstance(previous_drafts, dict) else {},
                }
                merged = {}
                for kind, older in values.items():
                    current = load_user_data(account, kind)
                    merged[kind] = ({**current, **{key: value for key, value in older.items()
                                                  if key not in current}}
                                    if kind == "profiles" else {**older, **current})
                    save_user_data(account, kind, merged[kind])
                choices = profile_choices(clean_catalog(merged["profiles"]))
                key, _ = active_profile(merged["profiles"])
                return (merged["profiles"], merged["tracking"], merged["feedback"],
                        merged["drafts"], gr.Dropdown(choices=choices, value=key),
                        gr.Dropdown(choices=choices, value=key),
                        "Import terminé. Tes données sont enregistrées dans ton compte.")

            import_browser_data.click(
                migrate_browser_data,
                inputs=[old_profiles, old_tracking, old_feedback, old_drafts],
                outputs=[profiles_state, internship_tracking, internship_feedback,
                         agent_drafts, profile_select, agent_profile_select,
                         import_browser_message],
            ).then(hydrate_profiles, inputs=[profiles_state],
                   outputs=[profile_select, agent_profile_select, *profile_editor,
                            profile_preview, agent_profile_card, agent_scan,
                            agent_empty_state]).then(
                show_internships, inputs=internship_inputs, outputs=internship_outputs)

        def reset_agent_for_profile(key: str, catalog: dict):
            record = selected_profile(catalog, key)
            return (render_profile_card(record) if record else "", "",
                    gr.Dropdown(choices=[], value=None), [],
                    "Analyse en cours…" if record else "", "", cleared_pdf(),
                    gr.Button(interactive=bool(record)),
                    gr.Column(visible=not bool(record)), gr.Column(visible=bool(record)),
                    gr.Button(interactive=False))

        def analyze_career_matches(profile_id: str, catalog: dict, feedback: dict, tracking: dict,
                                   request: gr.Request):
            record = selected_profile(catalog, profile_id)
            if not record:
                return ("", gr.Dropdown(choices=[], value=None), [], "", "", cleared_pdf(),
                        gr.Column(visible=False), gr.Button(interactive=False))
            if not record.get("target_roles"):
                return ('<div class="agent-empty">Metier cible manquant dans ce profil.</div>',
                        gr.Dropdown(choices=[], value=None), [],
                        "Renseigne les metiers recherches dans Mon profil, puis relance l'analyse.", "",
                        cleared_pdf(), gr.Column(visible=False), gr.Button(interactive=False))
            _, profile, goals = profile_prompt(record)
            jobs, _, _ = account_opportunities(request)
            candidate_pool = rank_candidate_pool(jobs, {}, tracking, profile=record)
            if not candidate_pool:
                return (render_briefs([]), gr.Dropdown(choices=[], value=None), [],
                        "Aucune offre recente et non postulee dans les sources actuelles. Actualise les offres puis reessaie.",
                        "", cleared_pdf(), gr.Column(visible=False), gr.Button(interactive=False))
            try:
                account = username(request) if shared else "local"
                cache_key = hashlib.sha256(json.dumps(
                    {"cache_version": 1, "model": os.getenv("INTERNSHIPS_AI_MODEL", "gpt-4o"),
                     "profile": record, "goals": goals,
                     "jobs": [asdict(job) for job in candidate_pool[:6]]},
                    ensure_ascii=False, sort_keys=True, default=str,
                ).encode("utf-8")).hexdigest()
                cache = load_user_data(account, "ai_cache")
                cached = cache.get(cache_key) if isinstance(cache, dict) else None
                if (isinstance(cached, dict) and
                        datetime.now(timezone.utc).timestamp() - float(cached.get("saved_at", 0)) < 24 * 3600):
                    current_jobs = {job.url: job for job in candidate_pool}
                    briefs = [{"job": current_jobs[item["url"]],
                               "evidence_quality": item["evidence_quality"],
                               "brief": item["brief"], "candidate_record": record}
                              for item in cached.get("briefs", [])
                              if isinstance(item, dict) and item.get("url") in current_jobs]
                else:
                    with llm_user(account), generation_slot():
                        briefs = generate_briefs(candidate_pool, profile, goals, candidate_record=record)
                    cache[cache_key] = {
                        "saved_at": datetime.now(timezone.utc).timestamp(),
                        "briefs": [{"url": item["job"].url,
                                    "evidence_quality": item["evidence_quality"],
                                    "brief": item["brief"]} for item in briefs],
                    }
                    if len(cache) > 10:
                        cache = dict(sorted(cache.items(), key=lambda item: item[1].get("saved_at", 0))[-10:])
                    save_user_data(account, "ai_cache", cache)
            except (CareerAgentError, GenerationLimitError) as exc:
                return ('<div class="agent-empty">Analyse interrompue.</div>',
                        gr.Dropdown(choices=[], value=None), [], str(exc), "",
                        cleared_pdf(), gr.Column(visible=False), gr.Button(interactive=False))
            if not briefs:
                return (render_briefs([]), gr.Dropdown(choices=[], value=None), [],
                        "Les annonces classees ne sont plus accessibles. Actualise les offres puis reessaie.",
                        "", cleared_pdf(), gr.Column(visible=False), gr.Button(interactive=False))
            selected = [row["job"] for row in briefs]
            choices = [(f"{job.company} - {job.title[:75]}", job.url) for job in selected]
            return (render_briefs(briefs), gr.Dropdown(choices=choices, value=selected[0].url),
                    [asdict(job) for job in selected],
                    f"{len(selected)} offre(s) analysee(s). Choisis une offre pour preparer ta lettre.", "",
                    cleared_pdf(), gr.Column(visible=True), gr.Button(interactive=False))

        agent_scan.click(
            analyze_career_matches,
            inputs=[agent_profile_select, profiles_state, internship_feedback, internship_tracking],
            outputs=[agent_briefs, agent_offer, agent_selection, agent_message, agent_letter, agent_pdf,
                     agent_workspace, agent_pdf_button],
            time_limit=105, show_progress="minimal",
        )

        def select_recommendation(rows: list[dict], evt: gr.EventData):
            url = str(getattr(evt, "url", "") or "")
            choices = [(f"{row.get('company', '')} - {str(row.get('title', ''))[:75]}", row["url"])
                       for row in (rows or []) if isinstance(row, dict) and row.get("url")]
            valid_url = url if any(choice[1] == url for choice in choices) else None
            return gr.Dropdown(choices=choices, value=valid_url), gr.Column(visible=bool(valid_url))

        agent_profile_select.input(
            reset_agent_for_profile, inputs=[agent_profile_select, profiles_state],
            outputs=[agent_profile_card, agent_briefs, agent_offer, agent_selection,
                     agent_message, agent_letter, agent_pdf, agent_scan,
                     agent_empty_state, agent_workspace, agent_pdf_button],
        ).then(
            analyze_career_matches,
            inputs=[agent_profile_select, profiles_state, internship_feedback, internship_tracking],
            outputs=[agent_briefs, agent_offer, agent_selection, agent_message, agent_letter,
                     agent_pdf, agent_workspace, agent_pdf_button],
            time_limit=105, show_progress="minimal",
        )
        agent_create_profile.click(lambda: gr.Tabs(selected="profil"), outputs=[primary_tabs])

        def prepare_career_letter(url: str, rows: list[dict], profile_id: str,
                                  catalog: dict, language: str, current_letter: str,
                                  request: gr.Request):
            row = next((item for item in (rows or []) if isinstance(item, dict) and item.get("url") == url), None)
            if row is None:
                message = "Cette offre n’a pas été reliée à la sélection. Choisis-la dans le menu puis réessaie."
                return gr.skip(), message, "", cleared_pdf(), gr.Button(interactive=bool((current_letter or "").strip()))
            record = selected_profile(catalog, profile_id)
            if not record:
                return gr.skip(), "Crée ton profil dans Mon profil avant de rédiger une lettre.", "", cleared_pdf(), gr.Button(interactive=bool((current_letter or "").strip()))
            try:
                name, profile, goals = profile_prompt(record)
                job = Opportunity(**row)
                with llm_user(username(request) if shared else "local"), generation_slot():
                    letter = generate_letter(job, profile, name, goals, language)
                if not letter or not letter.strip():
                    raise CareerAgentError("Le modèle a renvoyé une lettre vide. Réessaie dans un instant.")
                checks = "\n".join(f"- {item}" for item in letter_review_flags(job, letter, record))
            except Exception as exc:
                if isinstance(exc, (CareerAgentError, GenerationLimitError)):
                    message = str(exc)
                elif isinstance(exc, TimeoutError):
                    message = "La rédaction a dépassé le délai maximal. Réessaie dans un instant."
                else:
                    message = (f"La rédaction a échoué ({type(exc).__name__}). Tes modifications sont conservées ; "
                               "vérifie la connexion au modèle puis réessaie.")
                return gr.skip(), message, "", cleared_pdf(), gr.Button(interactive=bool((current_letter or "").strip()))
            return (letter, "Brouillon prêt. Relis et ajuste les faits avant l’envoi.",
                    (f"### À vérifier avant l’envoi\n\n{checks}" if checks else ""),
                    cleared_pdf(), gr.Button(interactive=True))

        def start_letter_generation():
            return ("Rédaction en cours…", gr.Button(value="Rédaction en cours…", interactive=False))

        def finish_letter_generation():
            return gr.Button(value="Préparer la lettre", interactive=True)

        agent_briefs.click(
            select_recommendation, inputs=[agent_selection], outputs=[agent_offer, agent_workspace],
        ).then(
            start_letter_generation, outputs=[agent_message, agent_letter_button], show_progress="hidden",
        ).then(
            prepare_career_letter,
            inputs=[agent_offer, agent_selection, agent_profile_select, profiles_state, agent_language,
                    agent_letter],
            outputs=[agent_letter, agent_message, agent_review, agent_pdf, agent_pdf_button],
            time_limit=105, show_progress="minimal",
        ).then(finish_letter_generation, outputs=[agent_letter_button], show_progress="hidden")

        agent_letter_button.click(
            start_letter_generation, outputs=[agent_message, agent_letter_button], show_progress="hidden",
        ).then(
            prepare_career_letter,
            inputs=[agent_offer, agent_selection, agent_profile_select, profiles_state, agent_language,
                    agent_letter],
            outputs=[agent_letter, agent_message, agent_review, agent_pdf, agent_pdf_button],
            time_limit=105, show_progress="minimal",
        ).then(
            finish_letter_generation, outputs=[agent_letter_button], show_progress="hidden",
        )

        def restore_agent_draft(url: str, drafts: dict, profile_id: str):
            letter = saved_letter(drafts, url, profile_id)
            if letter:
                return (letter, "Brouillon enregistré retrouvé. Tu peux le modifier ou le régénérer.", "",
                        gr.Button(interactive=bool(letter.strip())), cleared_pdf())
            return "", "Sélectionne cette offre puis prépare ta lettre.", "", gr.Button(interactive=False), cleared_pdf()

        agent_offer.input(
            restore_agent_draft, inputs=[agent_offer, agent_drafts, agent_profile_select],
            outputs=[agent_letter, agent_message, agent_review, agent_pdf_button, agent_pdf],
        )

        def update_export_state(letter: str):
            return gr.Button(interactive=bool((letter or "").strip())), cleared_pdf()

        agent_letter.input(update_export_state, inputs=[agent_letter], outputs=[agent_pdf_button, agent_pdf],
                           show_progress="hidden")

        def save_agent_draft(url: str, rows: list[dict], letter: str, drafts: dict,
                             profile_id: str, request: gr.Request):
            urls = {row.get("url") for row in (rows or []) if isinstance(row, dict)}
            try:
                updated = save_letter_draft(drafts, url, urls, letter, profile_id)
                if shared:
                    save_user_data(username(request), "drafts", updated)
            except CareerAgentError as exc:
                return drafts or {}, str(exc)
            return updated, ("Brouillon enregistré dans ton compte pour cette offre." if shared else
                             "Brouillon enregistré dans ce navigateur pour cette offre.")

        agent_save_draft.click(
            save_agent_draft, inputs=[agent_offer, agent_selection, agent_letter,
                                      agent_drafts, agent_profile_select],
            outputs=[agent_drafts, agent_message],
        )

        def download_career_letter(letter: str, url: str, rows: list[dict],
                                   profile_id: str, catalog: dict, language: str,
                                   request: gr.Request):
            row = next((item for item in (rows or []) if isinstance(item, dict) and item.get("url") == url), None)
            try:
                if row is None:
                    raise CareerAgentError("Sélectionne une offre avant l’export PDF.")
                record = selected_profile(catalog, profile_id) or {}
                path = export_letter_pdf(letter, str(row.get("company") or ""),
                                         candidate=str(record.get("name") or ""),
                                         job_title=str(row.get("title") or ""), language=language,
                                         candidate_record=record)
                register_download(username(request) if shared else "local", path)
                if shared:
                    return (f'<a class="private-pdf-download" href="/private-download" '
                            'rel="noopener">Télécharger le PDF · lien privé, valable 1 heure</a>',
                            "PDF prêt à télécharger.")
                return gr.File(value=path, visible=True), "PDF prêt à télécharger."
            except Exception as exc:
                message = str(exc) if isinstance(exc, CareerAgentError) else (
                    "La mise en page du PDF a échoué. Vérifie le texte de la lettre puis réessaie."
                )
                return cleared_pdf(), f"Export impossible : {message}"

        agent_pdf_button.click(
            download_career_letter, inputs=[agent_letter, agent_offer, agent_selection,
                                            agent_profile_select, profiles_state, agent_language],
            outputs=[agent_pdf, agent_message],
        )
        app_blocks.favicon_path = str(favicon_path)
        return app_blocks


