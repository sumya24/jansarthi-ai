import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint, type LocationOption, type WorkerSummary } from "../lib/api";
import { useToast } from "../lib/toast";
import { useModalA11y } from "../lib/useModalA11y";

/** Manually assigns a complaint to a specific worker — the fix for a complaint stuck "pending"
 * because assign_next_worker's automatic ward-matching (see backend/services/
 * assignment_service.py) found nobody eligible.
 *
 * The State/City/Ward filter above the worker dropdown is a narrowing aid, never a requirement --
 * with nothing picked, every worker is still listed (an admin overriding the automatic match is
 * exactly the case where "the ward has nobody" is the problem being worked around, so the full
 * list must stay reachable). It defaults to the COMPLAINT's own resolved location (`complaint.
 * state_id`/`district_id`/`ward_id`, see backend/routes/complaints.py's ComplaintResponse) one
 * level at a time, but only as far as a real worker actually exists there -- e.g. a Maharashtra
 * complaint defaults the State dropdown to Maharashtra only if at least one worker has
 * `state_id` set to that same state; otherwise that level is left on "All", same as if the
 * complaint's location hadn't resolved at all. Every level stays editable after that (same
 * cascading State -> City -> Ward pattern as Signup/Edit Worker, see WorkerLocationPicker.tsx),
 * so a ward with nobody in it can be widened back out to the city, the state, or everyone. */
export default function AssignWorkerModal({
  complaint,
  workers,
  onClose,
  onAssigned,
}: {
  complaint: Complaint;
  workers: WorkerSummary[];
  onClose: () => void;
  onAssigned: () => void;
}) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const [workerId, setWorkerId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const workerStateIds = useMemo(() => new Set(workers.map((w) => w.state_id).filter((v): v is number => v != null)), [workers]);
  const [stateId, setStateId] = useState<number | "">(() =>
    complaint.state_id != null && workerStateIds.has(complaint.state_id) ? complaint.state_id : ""
  );

  const workersInState = useMemo(() => (stateId === "" ? workers : workers.filter((w) => w.state_id === stateId)), [workers, stateId]);
  const districtIdsInState = useMemo(
    () => new Set(workersInState.map((w) => w.district_id).filter((v): v is number => v != null)),
    [workersInState]
  );
  const [districtId, setDistrictId] = useState<number | "">(() =>
    stateId !== "" && complaint.state_id === stateId && complaint.district_id != null && districtIdsInState.has(complaint.district_id)
      ? complaint.district_id
      : ""
  );

  const workersInDistrict = useMemo(
    () => (districtId === "" ? workersInState : workersInState.filter((w) => w.district_id === districtId)),
    [workersInState, districtId]
  );
  const wardIdsInDistrict = useMemo(
    () => new Set(workersInDistrict.map((w) => w.ward_id).filter((v): v is number => v != null)),
    [workersInDistrict]
  );
  const [wardId, setWardId] = useState<number | "">(() =>
    districtId !== "" && complaint.district_id === districtId && complaint.ward_id != null && wardIdsInDistrict.has(complaint.ward_id)
      ? complaint.ward_id
      : ""
  );

  const filteredWorkers = useMemo(
    () => (wardId === "" ? workersInDistrict : workersInDistrict.filter((w) => w.ward_id === wardId)),
    [workersInDistrict, wardId]
  );

  // A level the admin (or the default above) narrowed to can stop matching the currently picked
  // worker -- e.g. picking a different City after a Ward-level default. Falls back to "nothing
  // picked yet" rather than silently keeping a now-hidden worker selected.
  useEffect(() => {
    if (workerId !== "" && !filteredWorkers.some((w) => w.id === workerId)) setWorkerId("");
  }, [filteredWorkers, workerId]);

  const [states, setStates] = useState<LocationOption[]>([]);
  const [statesLoaded, setStatesLoaded] = useState(false);
  useEffect(() => {
    api.listStates().then((s) => { setStates(s); setStatesLoaded(true); }).catch(() => setStatesLoaded(true));
  }, []);
  const stateOptions = useMemo(() => states.filter((s) => workerStateIds.has(s.id)), [states, workerStateIds]);

  const [districts, setDistricts] = useState<LocationOption[]>([]);
  const [districtsLoaded, setDistrictsLoaded] = useState(false);
  useEffect(() => {
    if (stateId === "") { setDistricts([]); setDistrictsLoaded(true); return; }
    setDistrictsLoaded(false);
    api.listCitiesForState(stateId).then((d) => { setDistricts(d); setDistrictsLoaded(true); }).catch(() => setDistrictsLoaded(true));
  }, [stateId]);
  const districtOptions = useMemo(() => districts.filter((d) => districtIdsInState.has(d.id)), [districts, districtIdsInState]);

  const [wards, setWards] = useState<LocationOption[]>([]);
  const [wardsLoaded, setWardsLoaded] = useState(false);
  useEffect(() => {
    if (districtId === "") { setWards([]); setWardsLoaded(true); return; }
    setWardsLoaded(false);
    api.listWardsForCity(districtId).then((w) => { setWards(w); setWardsLoaded(true); }).catch(() => setWardsLoaded(true));
  }, [districtId]);
  const wardOptions = useMemo(() => wards.filter((w) => wardIdsInDistrict.has(w.id)), [wards, wardIdsInDistrict]);

  function handleStateChange(raw: string) {
    setStateId(raw === "" ? "" : Number(raw));
    setDistrictId("");
    setWardId("");
  }
  function handleDistrictChange(raw: string) {
    setDistrictId(raw === "" ? "" : Number(raw));
    setWardId("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token || workerId === "") return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.assignComplaint(token, complaint.id, workerId);
      toast.success(`${t(lang, "admin.assignedToast")} ${result.assigned_worker_name ?? ""}`);
      onAssigned();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "admin.assignErrFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && !saving && onClose()}>
      <div ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="jm-modal-title" tabIndex={-1}>
        <div className="modal-head">
          <h3 className="display" id="jm-modal-title">{t(lang, "admin.assignModalTitle")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose} disabled={saving}>
            ✕
          </button>
        </div>

        {error && <div className="banner-error">{error}</div>}

        {workers.length === 0 ? (
          <p style={{ fontSize: 13.5, color: "var(--ink-2)" }}>{t(lang, "admin.assignModalNoWorkers")}</p>
        ) : (
          <form onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label htmlFor="assign-filter-state">{t(lang, "signup.homeLocation.state")}</label>
              <select
                id="assign-filter-state"
                value={stateId}
                disabled={!statesLoaded}
                onChange={(e) => handleStateChange(e.target.value)}
              >
                <option value="">{t(lang, "admin.assignFilterAllStates")}</option>
                {stateOptions.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="assign-filter-district">{t(lang, "signup.homeLocation.city")}</label>
              <select
                id="assign-filter-district"
                value={districtId}
                disabled={stateId === "" || !districtsLoaded}
                onChange={(e) => handleDistrictChange(e.target.value)}
              >
                <option value="">{t(lang, "admin.assignFilterAllCities")}</option>
                {districtOptions.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="assign-filter-ward">{t(lang, "signup.homeLocation.ward")}</label>
              <select
                id="assign-filter-ward"
                value={wardId}
                disabled={districtId === "" || !wardsLoaded}
                onChange={(e) => setWardId(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">{t(lang, "admin.assignFilterAllWards")}</option>
                {wardOptions.map((w) => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
            </div>

            <div className="field">
              <label htmlFor="assign-worker-select">{t(lang, "admin.assignModalLabel")}</label>
              {filteredWorkers.length === 0 ? (
                <p style={{ fontSize: 12.5, color: "var(--ink-2)", margin: "4px 0 0" }}>{t(lang, "admin.assignFilterNoWorkers")}</p>
              ) : (
                <select
                  id="assign-worker-select"
                  value={workerId}
                  onChange={(e) => setWorkerId(e.target.value ? Number(e.target.value) : "")}
                >
                  <option value="">{t(lang, "admin.assignModalPlaceholder")}</option>
                  {filteredWorkers.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.full_name} — {w.ward}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div className="modal-actions">
              <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
                {t(lang, "addWorker.cancel")}
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving || workerId === ""}>
                {saving ? t(lang, "admin.assigning") : t(lang, "admin.assignModalSubmit")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
