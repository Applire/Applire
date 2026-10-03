
# Strawberry API contract — services (W0, frozen interfaces F5–F9)

Companion to `api-contract-strawberry.md` (REST shapes, F1–F4). This file
freezes the **service-layer** contracts the later waves code against
(ADR-092 cl. 2, 5(f), 11, 14, 15). Changing one of them is a
`CONTRACT-CHANGE` to the main session, not a local edit.

## Services (F5–F9)

### W0 rule

Every signature below **accepts and ignores** `user_id` in W0 — zero
behaviour change. The W2 owner of each module (3b, 3c, 3d, 4a) makes it
required and uses it; the MCP door (4b) and the routers pass it. The
parameter is keyword-only (`*, user_id: uuid.UUID | None = None`) unless the
function already had a `user_id` parameter, which then stays as it is.
`tests/unit/test_service_user_id_signatures.py` fails on any public function
in an F5 module (and on any listed background task or profile read-path
function) that does not accept `user_id` by keyword.

### F5 — generation-service signatures (keyword added in W0)

Modules: `cv`, `cover_letter`, `session`, `gap`, `review_actions`,
`review_rewrite`, `cv_section_editor`, `cv_assist`, `cv_diff`, `signature`,
`photo`, `fact_pins`, `matching`, `color_detection`, `application`, `job`,
`documents`, `gap_jobs`, `flow/orchestrator` — every public module-level
function — plus the ADR-092 cl. 14 background-task functions
(`cv._render_cv_background`, `cv._update_ats_report_by_id`,
`cover_letter._render_cover_letter_background`,
`cover_letter._update_ats_report_letter_by_id`). Profile read-path functions
that resolve "the latest profile" (F6 neighbours, for the MCP door and 3b)
got the same keyword and are listed under `applire.services.profile*`.

`photo` and `documents` needed no change (every public function already
takes `user_id`). There is no `services/settings.py`: ADR-092 cl. 14's
"settings" is `routers/settings.py` (3b's), not a service module.

#### `applire.services.cv` (23)

- `classify_generation_error(exc: BaseException, *, user_id: uuid.UUID | None = None) -> str`
- `assemble_tailored_cv(prose: dict, profile_json: dict, *, user_id: uuid.UUID | None = None) -> dict`
- `async generate_cv_segmented(job_analysis: dict, profile: dict, keyword_gaps: list[str], *, output_language: str, provider: 'LLMProvider', keyword_ledger: list[dict] | None = None, budget: 'BudgetResult | None' = None, stated_limits_block: str | None = None, scope_positioning_block: str | None = None, vault_evidence_items: 'list | None' = None, pinned_facts_block: str | None = None, user_id: uuid.UUID | None = None) -> dict`
- `format_place_date_for_cv(location: str | None, language: str, *, user_id: uuid.UUID | None = None) -> str`
- `async generate_cv(job_id: uuid.UUID, db: AsyncSession, provider: LLMProvider, background_tasks: BackgroundTasks | None = None, template: Literal['classic_german', 'modern_swiss', 'executive', 'tech_developer', 'creative_sidebar', 'academic', 'compact_pro'] = 'classic_german', base_url: str = 'http://localhost:8001', target_pages: int | None = None, *, user_id: uuid.UUID | None = None) -> CVGenerateResponse`
- `async get_cv_status(cv_id: uuid.UUID, db: AsyncSession, base_url: str, *, user_id: uuid.UUID | None = None) -> CVStatusResponse`
- `async set_cv_signature_override(cv_id: uuid.UUID, override: bool | None, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bool`
- `async list_cvs_for_job(job_id: uuid.UUID, db: AsyncSession, base_url: str, *, user_id: uuid.UUID | None = None) -> list[CVStatusResponse]`
- `filename_part(value: str | None, *, user_id: uuid.UUID | None = None) -> str`
- `compose_document_filename(*parts: str | None, suffix: str = '', fallback: str, extension: str = 'pdf', user_id: uuid.UUID | None = None) -> str`
- `async get_pdf_filename(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str`
- `project_has_content(project: Any, *, user_id: uuid.UUID | None = None) -> bool`
- `strip_empty_projects(tailored: TailoredCVData, *, user_id: uuid.UUID | None = None) -> TailoredCVData`
- `async get_cv_html(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str`
- `async get_cv_pdf(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bytes`
- `async get_cv_docx(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bytes`
- `async get_docx_filename(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str`
- `async _render_cv_background(cv_id: uuid.UUID, job_id: uuid.UUID, profile_id: uuid.UUID, template: Literal['classic_german', 'modern_swiss', 'executive', 'tech_developer', 'creative_sidebar', 'academic', 'compact_pro'], application_id: uuid.UUID | None = None, *, user_id: uuid.UUID | None = None) -> None`
- `async _update_ats_report_by_id(cv_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> None`
- `async get_cv_ats_report(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> 'ATSReportResponse'`
- `async get_cv_truthfulness_report(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> 'TruthfulnessReportResponse'`
- `async get_cv_critic_report(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> 'OutcomeCriticReportResponse'`
- `async render_agent_cv(content: dict, job_id: uuid.UUID, db: AsyncSession, template: Literal['classic_german', 'modern_swiss', 'executive', 'tech_developer', 'creative_sidebar', 'academic', 'compact_pro'] = 'classic_german', target_pages: int | None = None, *, user_id: uuid.UUID | None = None) -> GeneratedCV`

#### `applire.services.cover_letter` (16)

- `async generate_cover_letter(request: CoverLetterGenerateRequest, db: AsyncSession, provider: LLMProvider, background_tasks: BackgroundTasks | None = None, base_url: str = 'http://localhost:8001', *, user_id: uuid.UUID | None = None) -> CoverLetterGenerateResponse`
- `async get_cover_letter_status(cl_id: uuid.UUID, db: AsyncSession, base_url: str, *, user_id: uuid.UUID | None = None) -> CoverLetterStatusResponse`
- `async set_cover_letter_signature_override(cl_id: uuid.UUID, override: bool | None, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bool`
- `async get_cover_letter_pdf_filename(cl_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str`
- `async get_cover_letter_html(cl_id: uuid.UUID, db: AsyncSession, require_ready: bool = True, *, user_id: uuid.UUID | None = None) -> str`
- `async get_cover_letter_docx(cl_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bytes`
- `async get_cover_letter_docx_filename(cl_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str`
- `build_stated_limits_entry(denied_concepts: list[dict] | None, keyword_ledger: list[dict] | None, *, user_id: uuid.UUID | None = None) -> dict | None`
- `async patch_cover_letter_section(cl_id: uuid.UUID, section: str, content: str, db: AsyncSession, background_tasks: BackgroundTasks | None = None, *, user_id: uuid.UUID | None = None) -> None`
- `async get_cover_letter_by_job(job_id: uuid.UUID, db: AsyncSession, base_url: str, *, user_id: uuid.UUID | None = None) -> CoverLetterStatusResponse`
- `async _render_cover_letter_background(cl_id: uuid.UUID, cv_id: uuid.UUID | None, job_id: uuid.UUID, application_id: uuid.UUID | None = None, *, user_id: uuid.UUID | None = None) -> None`
- `async _update_ats_report_letter_by_id(cl_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> None`
- `async get_cover_letter_ats_report(cl_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> 'ATSReportResponse'`
- `async get_cover_letter_truthfulness_report(cl_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> 'TruthfulnessReportResponse'`
- `async get_cover_letter_critic_report(cl_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> 'OutcomeCriticReportResponse'`
- `async render_agent_letter(content: dict, job_id: uuid.UUID, db: AsyncSession, template: str = 'classic_german', *, user_id: uuid.UUID | None = None) -> GeneratedCoverLetter`

#### `applire.services.session` (15)

- `async get_ui_language(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str`
- `async get_conversation_language(db: AsyncSession, job_id: uuid.UUID | str | None = None, *, user_id: uuid.UUID | None = None) -> str`
- `render_confirmation(pending_conf: dict, lang: str, *, user_id: uuid.UUID | None = None) -> tuple[str, list[str]]`
- `async create_profile_review_session(db: AsyncSession, provider: LLMProvider, lang: str | None = None, *, user_id: uuid.UUID | None = None) -> SessionCreateResponse`
- `async gap_cluster_ids(job_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> list[str] | None`
- `is_micro_session(record: InterviewSession, *, user_id: uuid.UUID | None = None) -> bool`
- `async active_full_interview_exists(job_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bool`
- `gap_record_copy(key: str, lang: str = 'en', *, user_id: uuid.UUID | None = None, **fields: object) -> str`
- `gap_not_askable(cluster: dict, lang: str = 'en', *, user_id: uuid.UUID | None = None) -> GapNotAskableError | None`
- `async last_recorded_answer(job_id: uuid.UUID, cluster_id: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> str | None`
- `same_testimony(a: str | None, b: str | None, *, user_id: uuid.UUID | None = None) -> bool`
- `async cluster_coverage_for(job_id: uuid.UUID, cluster_id: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ClusterCoverage | None`
- `async create_session(request: SessionCreateRequest, db: AsyncSession, provider: LLMProvider, *, user_id: uuid.UUID | None = None) -> SessionCreateResponse`
- `async send_message(session_id: uuid.UUID, message: str, db: AsyncSession, provider: LLMProvider, *, user_id: uuid.UUID | None = None) -> SessionMessageResponse`
- `async get_session_state(session_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> SessionStateResponse`

#### `applire.services.gap` (13)

- `gap_relevant_profile(profile_json: dict | None, *, user_id: uuid.UUID | None = None) -> dict`
- `analysis_inputs_changed(row: GapAnalysis, job: JobAnalysis, profile: MasterProfile, *, user_id: uuid.UUID | None = None) -> bool`
- `async stored_analysis_inputs_changed(row: GapAnalysis, job: JobAnalysis, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> bool`
- `async analyze_gaps(job_id: uuid.UUID, db: AsyncSession, provider: LLMProvider, *, answer_scope: AnswerScope | None = None, user_id: uuid.UUID | None = None) -> GapAnalysisResponse`
- `async analyze_gaps_for_session(session_id: uuid.UUID, db: AsyncSession, provider: LLMProvider, *, user_id: uuid.UUID | None = None) -> GapAnalysisResponse`
- `async downgrade_keyword_liability(job_id: uuid.UUID, concept: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> GapAnalysisResponse`
- `async set_cluster_left_open(job_id: uuid.UUID, cluster_id: str, left_open: bool, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> GapAnalysis`
- `askable_gap_inputs(gap_analysis: GapAnalysis, *, user_id: uuid.UUID | None = None) -> list`
- `has_clustering_input(gap_analysis: GapAnalysis, *, user_id: uuid.UUID | None = None) -> bool`
- `async cluster_gaps(gap_analysis: GapAnalysis, job: JobAnalysis, provider: LLMProvider, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None`
- `ledger_input_from_classification(c: dict[str, Any], *, user_id: uuid.UUID | None = None) -> dict[str, Any]`
- `pair_rows_by_requirement(fresh: list[Any], previous: list[Any], jd_terms: list[str], *, user_id: uuid.UUID | None = None) -> list[int | None]`
- `merge_ledger_per_requirement(fresh: list[dict[str, Any]], previous: list[dict[str, Any]] | None, *, jd_terms: list[str], touched_members: list[str], user_id: uuid.UUID | None = None) -> tuple[list[dict[str, Any]], list[int]]`

#### `applire.services.review_actions` (13)

- `async load_document(kind: 'Kind', doc_id: 'uuid.UUID', db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None)`
- `async reaudit(kind: 'Kind', record, db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'None'`
- `findings_of(record, *, user_id: 'uuid.UUID | None' = None) -> 'list[GroupOneFinding]'`
- `async patchable_sections(kind: 'Kind', record, db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'list[tuple[str, str]]'`
- `async write_section(kind: 'Kind', record, section_id: 'str', content: 'str', db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'None'`
- `async protected_names(kind: 'Kind', record, db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'list[str]'`
- `protected_name_hit(section_text: 'str', wording: 'list[str]', names: 'list[str]', *, user_id: 'uuid.UUID | None' = None) -> 'str | None'`
- `async add_evidence(kind: 'Kind', doc_id: 'uuid.UUID', key: 'str', text: 'str', db: 'AsyncSession', provider, *, user_id: 'uuid.UUID | None' = None) -> 'ActionOutcome'`
- `async sibling_document_id(kind: 'Kind', record, db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'tuple[Kind, uuid.UUID] | None'`
- `async take_out(kind: 'Kind', doc_id: 'uuid.UUID', key: 'str', db: 'AsyncSession', provider, *, user_id: 'uuid.UUID | None' = None) -> 'ActionOutcome'`
- `async undo(kind: 'Kind', doc_id: 'uuid.UUID', key: 'str', db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'ActionOutcome'`
- `async edited(kind: 'Kind', doc_id: 'uuid.UUID', key: 'str', db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'ActionOutcome'`
- `async walked(kind: 'Kind', doc_id: 'uuid.UUID', db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'ActionOutcome'`

#### `applire.services.review_rewrite` (4)

- `form_present(form: 'str', text: 'str', *, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `figure_present(figure: 'str', text: 'str', *, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `find_occurrences(forms: 'list[str]', text: 'str', *, user_id: 'uuid.UUID | None' = None) -> 'list[str]'`
- `async rewrite_for_removal(kind: "Literal['cv', 'cover_letter']", record: 'Any', section_id: 'str', section_text: 'str', forms: 'list[str]', provider: 'LLMProvider', *, language: 'str', figures_only: 'bool' = False, user_id: 'uuid.UUID | None' = None) -> 'RemovalRewrite'`

#### `applire.services.cv_section_editor` (5)

- `build_content_snapshot(tailored: TailoredCVData, *, user_id: uuid.UUID | None = None) -> dict`
- `apply_overrides_to_tailored(tailored: TailoredCVData, content_snapshot: dict | None, section_overrides: dict | None, *, user_id: uuid.UUID | None = None) -> TailoredCVData`
- `async get_cv_sections(cv_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> CVSectionsResponse`
- `async patch_cv_section(cv_id: uuid.UUID, section_id: str, content: str, save_to_profile: bool, db: AsyncSession, background_tasks: BackgroundTasks | None = None, *, user_id: uuid.UUID | None = None) -> SectionPatchResponse`
- `build_section_field_edit(section_id: str, content: str, *, profile_data: 'MasterProfileData', content_snapshot: dict | None, lang: str, user_id: uuid.UUID | None = None) -> tuple[str, object] | None`

#### `applire.services.cv_assist` (3)

- `async start_assist_session(cv_id: uuid.UUID, section_id: str, gap_id: str, provider: LLMProvider, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> AssistStartResponse`
- `async submit_assist_answer(cv_id: uuid.UUID, section_id: str, session_id: str, answer: str, provider: LLMProvider, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> AssistAnswerResponse`
- `async rewrite_section(cv_id: uuid.UUID, section_id: str, directions: str, gap_ids: list[str], provider: LLMProvider, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> RewriteResponse`

#### `applire.services.cv_diff` (2)

- `compute_cv_profile_diff(tailored: dict, profile: dict, *, user_id: uuid.UUID | None = None) -> list[FieldChange]`
- `async get_cv_profile_diff(cv_id, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> CVProfileDiffResponse`

#### `applire.services.signature` (5)

- `async resolve_signature_data_uri(db: 'AsyncSession', *, document: 'DocumentKind', override: 'bool | None' = None, user_id: 'uuid.UUID | None' = None) -> 'str | None'`
- `async resolve_signature_bytes(db: 'AsyncSession', *, document: 'DocumentKind', override: 'bool | None' = None, user_id: 'uuid.UUID | None' = None) -> 'bytes | None'`
- `async resolve_signature_available(db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `async resolve_signature_effective(db: 'AsyncSession', *, document: 'DocumentKind', override: 'bool | None' = None, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `format_place_date(location: 'str | None', language: 'str', today: 'date | None' = None, *, user_id: 'uuid.UUID | None' = None) -> 'str'`

#### `applire.services.fact_pins` (6)

- `check_target_renderable(request, *, user_id: 'uuid.UUID | None' = None) -> 'None'`
- `entry_is_claimable(entry, *, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `quote_resolves_in_entry(quote: 'str', entry, entry_type: 'str', *, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `pin_resolves(pin: 'FactPin', profile: 'MasterProfileData', *, user_id: 'uuid.UUID | None' = None) -> 'bool'`
- `refresh_pin_staleness(pins: 'list[FactPin]', profile: 'MasterProfileData', *, user_id: 'uuid.UUID | None' = None) -> 'tuple[list[FactPin], bool]'`
- `load_pins(application, *, user_id: 'uuid.UUID | None' = None) -> 'list[FactPin]'`

#### `applire.services.matching` (2)

- `async compute_similarity(job_id: uuid.UUID, profile_id: uuid.UUID, db: AsyncSession, embedding_provider: EmbeddingProvider, *, user_id: uuid.UUID | None = None) -> float`
- `async rank_jobs(profile_id: uuid.UUID, db: AsyncSession, top_n: int = 10, berufsbild_code: str | None = None, *, user_id: uuid.UUID | None = None) -> list[JobMatchResult]`

#### `applire.services.color_detection` (4)

- `derive_tint(hex_color: str, *, user_id: uuid.UUID | None = None) -> str`
- `derive_surface_text(hex_color: str, *, user_id: uuid.UUID | None = None) -> str`
- `async resolve_color_context(record: 'GeneratedCV', db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ColorContext`
- `async detect_and_cache_company_color(job: 'JobAnalysis', db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None`

#### `applire.services.application` (1)

- `async sync_workflow_status(application_id: uuid.UUID, new_step: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None`

#### `applire.services.job` (1)

- `async analyze_jd(text: str, db: AsyncSession, provider: LLMProvider, source_url: str | None = None, embedding_provider: EmbeddingProvider | None = None, role_title_override: str | None = None, company_name_override: str | None = None, *, user_id: uuid.UUID | None = None) -> JobAnalysisResponse`

#### `applire.services.gap_jobs` (1)

- `classify_gap_error(exc: BaseException, *, user_id: uuid.UUID | None = None) -> str`

#### `applire.services.flow.orchestrator` (3)

- `unrecordable_artifact_notice(step: str, *, user_id: uuid.UUID | None = None) -> str`
- `async repoint_flow_gap_analysis(job_id: uuid.UUID | None, gap_analysis_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None`
- `async advance_flow_on_interview_complete(interview_session_id: uuid.UUID, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> None`

#### `applire.services.profile` (8)

- `async get_profile(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> MasterProfileResponse | None`
- `async profile_exists(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> dict`
- `async patch_profile_section(section: str, value: object, db: AsyncSession, source: str = 'manual_edit', source_session_id: str | None = None, provider: LLMProvider | None = None, basis_updated_at: datetime.datetime | None = None, *, user_id: uuid.UUID | None = None) -> MasterProfileResponse`
- `async get_enrichment_history(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> list[EnrichmentRecord]`
- `async get_profile_changes(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ProfileChangesResponse`
- `async get_profile_health(db: AsyncSession, *, user_id: uuid.UUID | None = None) -> ProfileHealthResponse`
- `async resolve_conflict(conflict_id: str, resolution: str, value: object, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> MasterProfileResponse`
- `async resolve_confirmation(confirmation_id: str, chosen_option: str, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> MasterProfileResponse`

#### `applire.services.profile.snapshots` (1)

- `async undo_last_merge(db: 'AsyncSession', *, user_id: 'uuid.UUID | None' = None) -> 'UndoResult'`

#### `applire.services.profile.role_add` (1)

- `async add_role_to_profile(req: AddRoleRequest, db: AsyncSession, *, user_id: uuid.UUID | None = None) -> AddRoleResponse`

#### `applire.services.profile.reconcile.agent_bridge` (1)

- `async submit_agent_claims(submission: 'ClaimsSubmission', job_id: 'uuid.UUID | None', db: 'AsyncSession', provider: 'LLMProvider', *, user_id: 'uuid.UUID | None' = None) -> 'SubmissionResult'`

#### `applire.services.profile.reconcile.testimony_bridge` (1)

- `async submit_testimony(text: 'str', db: 'AsyncSession', provider: 'LLMProvider', lang: 'str' = 'en', *, user_id: 'uuid.UUID | None' = None) -> 'TestimonyResult'`

#### Already accepted `user_id` before W0 (unchanged)

`signature.upload_signature`, `signature.delete_signature`,
`signature.get_signature_bytes`, `photo.upload_photo`, `photo.delete_photo`,
`photo.get_photo_bytes`, `fact_pins.add_fact_pin`, `fact_pins.remove_fact_pin`,
`application.get_application_for_job`, `application.create_application`,
`application.list_applications`, `application.get_application`,
`application.patch_application`, `application.delete_application`,
`application.start_application_workflow`, `application.mark_application_hired`,
`application.find_duplicate_application`, `documents.list_documents`,
`gap_jobs.create_gap_job`, `gap_jobs.get_gap_job`,
`gap_jobs.run_gap_job_background`, `flow.orchestrator.create_flow`,
`flow.orchestrator.get_flow_state`, `flow.orchestrator.advance_flow`,
`profile.import_jobs.run_import_job_background`, `profile.import_from_pdf`,
`profile.import_from_text`, `profile.import_from_linkedin`,
`profile.import_from_linkedin_zip`, `profile.import_from_linkedin_pdf`,
`profile.list_open_gates`, `profile.ingest_cv`, `profile.upload_cv`,
`profile.resolve_staged_extraction`.

### F6 — profile read path

- `async applire.services.profile.get_profile_for_user(db: AsyncSession, user_id: uuid.UUID | None = None) -> MasterProfile | None`
  — W0 body delegates to `_get_latest(db)` (newest live row,
  `created_at DESC`). 3b replaces the body with the owner-keyed read and moves
  the 26 latest-profile sites onto it.
- `async applire.services.profile.commit.create_profile_record(db: AsyncSession, user_id: uuid.UUID | None = None) -> MasterProfile`
  — `user_id` accepted, ignored; 3b sets `MasterProfile.user_id` and adds the
  savepoint/`IntegrityError` re-read (ADR-092 cl. 2).

### F7 — posting labels (working, not yet wired)

- `applire.services.posting_labels.effective_posting_labels(job, application) -> tuple[str, str | None]`
  — returns `(role_title, company_name)`. An application value wins when it is
  a non-blank string; otherwise the posting's value stands; `application=None`
  returns the posting's values. Readers of `job.role_title` /
  `job.company_name` are wired by the W2 owner of each file.

### F8 — safe fetch

- `async applire.services.safe_fetch.safe_get(url: str, *, timeout: float | httpx.Timeout, headers: Mapping[str, str] | None, max_redirects: int = 5) -> httpx.Response`
  — W0 body is a passthrough (`httpx.AsyncClient(follow_redirects=True, max_redirects=…)`).
- `applire.services.safe_fetch.UnsafeFetchRefused(Exception)` — the exception
  4a raises for a refused address or redirect hop; callers (scraper, colour
  detection) catch this name. Not raised in W0.

### F9 — erasure

- `async applire.services.erasure.erase(db: AsyncSession, user_id: uuid.UUID, scope: ErasureScope) -> dict[str, int]`
  with `ErasureScope = Literal["vault", "account"]`; returns per-table
  deleted-row counts (today's `records_deleted`).
  - `"vault"`: W0 delegates to today's `routers.profile.erase_profile` handler
    body (no router code moved) — today's single-user semantics, unchanged.
  - `"account"`: raises `NotImplementedError` in W0 (3b fills it; 1b's
    `DELETE /api/me/account` calls it).
  - unknown scope: `ValueError`.
- `applire.services.erasure.ErasureFailed(Exception)` — the transaction rolled
  back, nothing was deleted (the router maps it to 500).
