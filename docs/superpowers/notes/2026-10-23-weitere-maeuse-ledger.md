# SDD ledger — plan: docs/superpowers/plans/2026-10-23-razertux-weitere-maeuse.md

Spec: docs/superpowers/specs/2026-10-23-razertux-weitere-maeuse-design.md (approved 2026-10-23). Branch feat/naga-config, start HEAD fdd18c7.
Execution: subagent-driven (user chose "Variante 2" earlier). User approved plan implicitly ("Platten nicht mehr. Test entfällt").

## Pre-flight scan
| Pair / Task | Produces → Consumes | Finding |
|---|---|---|
| T1→T2 | T1 importer filters by `commands.BUILDERS` → created only in T2 | ORDER CONFLICT → Ruling M1 |
| T1→T3 | DeviceInfo/Command/load_devices/by_pid → RazerMouse | consistent |
| T2→T3 | BUILDERS/PARSERS → RazerMouse | consistent |
| T3→T4 | RazerMouse.supports → probe | consistent |
| T4→T6 | backup_path(model, root) ↔ migration of mouse-backup.json → backups/naga_v2_pro.json | consistent |
| T5→T6 | InputSignal, ButtonId.LEARN_n → DeviceSettings.learned | consistent |
| T5→T10 | button_maps.py gets plate tables in T10 | consistent (T10 modifies T5 file) |
| T6→T7 | ModelStore, DeviceSettings.remap_enabled → needs_grab | consistent |
| T7→T8 | sessions, learning callbacks → Daemon1 | consistent |
| T7→T13 | session applies Profile.scroll (T13 adds field + apply) | consistent |
| T8→T12 | GetDiagnostics/StartLearning/ButtonLearned → DeviceBar/LearnDialog | consistent |
| T9→T10/T11 | laserize + layout format → assets | consistent; T10 deletes naga side svg used by T9's interim Naga layout (fine, T10 replaces layout) |
| T1→T14 | importer --udev → rules | consistent |
| T1 self | test sum(pids)>=117 vs 117 classes | consistent |
| T2 self | example byte tests must be re-derived from OpenRazer C (plan says so) | ok |
| T3–T14 self | tests vs files | no contradictions found |

- Ruling M1: T1 importer records every builder fn name it finds (no filtering by BUILDERS); unknown fns are dropped at runtime by RazerMouse (T3: Command whose fn is not in BUILDERS → feature unsupported). Importer still prints a summary of distinct fn names to stderr. — why: BUILDERS does not exist until T2; "nie geraten" still holds because unknown fns are never executed. — cost if wrong: DB slightly larger; a test in T3 must cover "unknown fn → unsupported".
- Task 1: dispatched (BASE fdd18c7, implementer ac5da667515c2a3fe, sonnet)
- Task 1: implementer DONE_WITH_CONCERNS (99f0b1c; 88 models/117 PIDs; concerns: multi-packet bodies record only first builder (set_led_state on 99 devices); 8 keys differ per PID in a model (first PID wins); naga thumbgrid effects need raw 0x0f/0x02 pre-packet; abyssus dpi_max 0). Review dispatched (a3f3f333bcf093960).
- Task 1: review Not approved — I1 multi-packet bodies stored as single fn; I2 per-PID differences collapsed (first PID wins); I3 features union vs per-PID protocol.
- Ruling M2: Command gains `steps: tuple[str, ...]` = every builder call in the body in order, raw preludes as "raw:0xCC/0xII/size" entries; `fn` stays = steps[0]. Runtime (T3) runs a Command only if every step is known, else feature unsupported. — why: keeps "nie geraten" in data, lets T2/T3 implement sequences. — cost if wrong: schema change later.
- Ruling M3: DeviceInfo.protocol/features hold only what is identical for all PIDs of a model; new `pid_protocol: dict[int, dict[str, Command]]` and `pid_features: dict[int, frozenset[str]]` hold per-PID extras/differences; `DeviceInfo.for_pid(pid) -> DeviceInfo` returns the flattened view for a connected PID. — why: wired/wireless differ (battery, txn). — cost if wrong: small API churn in T3.
- Task 1: minor (deferred): cmd_for falls back to default for blocked PIDs (no current occurrence); parse_switch ignores `fallthrough;` (no current occurrence); class_kind defaults unknown suffix to wired; abyssus dpi_max 0 (T3 must treat 0 as hide); hand-written __hash__ in db.py; repro test only runs with local clone.
- Ruling M2a: `fn` = first named builder (not a raw step) — why: BUILDERS lookup by name — cost if wrong: none.
- Task 1: minor (deferred): zones still union over PIDs.
- Task 1: fix round 1/5 (3 addressed, 0 open; commits 99f0b1c..57effe2)
- Task 1: complete (commits fdd18c7..57effe2, review clean)
- Task 2: dispatched (BASE 57effe2, implementer a9ab747fbc2466514, sonnet)
- Task 2: implementer DONE (69723d1; 65 builders, 18 parsers; orochi2011 builders NOT_PORTABLE; C set_dpi_xy always VARSTORE -> generic set_dpi is persistent: T3 must treat it as PERSISTENT and keep backup write-back; set_device_mode clamps to 0/3)
- Task 2: review Approved (reviewer's "-W error crash" was its env; controller re-ran: 698 passed with QT_QPA_PLATFORM=offscreen -W error)
- Task 2: minor (deferred): builder clamps (wave dir 0..2, reactive speed 1..4) could silently change Naga bytes if constants change; set_device_mode clamps unknown modes to 0 (suggest ValueError in Naga wrapper); naga _POLL_BYTE duplicate of commands._POLL_BYTE (add comment); BUILDERS assembled via globals() prefix scan; builder tests hand-derived (not independent).
- Task 2: complete (commits 57effe2..69723d1, review clean)
- Ruling M4: `steps` can mean SEQUENCE (led_state/rgb/effect) or ALTERNATIVES (breathing single/dual/random); T3 implements an explicit pattern table, unknown patterns unsupported — why: importer cannot tell them apart — cost if wrong: some effects hidden.
- Task 3: dispatched (BASE 69723d1, implementer ac6c6a8d622aae5ce, opus)
- Task 3: implementer DONE_WITH_CONCERNS (eed1ec2; 754 passed; raw-step patterns unsupported; RIGHT_SIDE_LED=0x10/LEFT=0x11 per header; poll-rate set unsupported on 6 models (2nd packet not in DB); orochi_2013/imperator DPI read VARSTORE hard-coded; dpi_xy_byte unsupported; generic lighting/stages default NOSTORE untested; tray zone labels only logo/scroll/thumb)
- Task 3: review Approved with 1 Important — _none_branch sends standard_matrix_effect_none with Command.txn (0x3F) but OpenRazer uses 0xFF for that packet (orochi backlight:none).
- Ruling M5: effect patterns may carry a per-packet txn override when the OpenRazer handler uses a different txn for a packet than the Command's txn (e.g. 0xFF default); Command.txn stays the default. — why: match OpenRazer bytes exactly — cost if wrong: one table field.
- Task 3: minor (deferred): generic classic-LED sequences use NOSTORE while OpenRazer writes VARSTORE (untested on hw); duplicated VARSTORE/NOSTORE/ZONE_LED constants in naga_v2_pro.py vs mouse.py; no dedicated test for set_dpi_stages persistent flag; matrix-zone standard set_led_* quietly unsupported.
- Task 3: fix round 1/5 (1 addressed, 0 open; commits eed1ec2..528f261)
- Task 3: complete (commits 69723d1..528f261, review clean)
- Task 4: dispatched (BASE 528f261, implementer a2b24c6a5df480cdc, sonnet)
- Task 4: implementer DONE (538c2d2; 765 passed; no RED run (TDD deviation); ProbeResult.raw extra; probe swaps mouse._t temporarily; firmware zero check vs "v0.0")
- Task 4: review Approved-with-fixes, 1 Important (failing responses not recorded: real Transport raises RazerError, so diagnostics lack raw bytes for the unsupported case)
- Task 4: minor (deferred): dead `if bad: continue`; firmware-zero check coupled to "v0.0" string; no test for feature-absent skip / checked==0; unused RazerError import in test; docstring note that probe is not thread-safe (swaps mouse._t)
- Task 4: fix round 1/5 (1 addressed, 0 open; commits 538c2d2..c52cac4)
- Task 4: minor (deferred): leftover dead `bad = True` in probe.py
- Task 4: complete (commits 528f261..c52cac4, review clean)
- Task 5: dispatched (BASE c52cac4, implementer ae40d0174969eef5d, sonnet)
- Task 5: implementer DONE (b7cdc98; 784 passed; fallback now yields ifNN_event for non-kbd if02 nodes (was None); NAGA_DEVICE_SUFFIXES unused but exported)
- Task 5: review Approved, 1 Important (fallback grabs previously ignored non-kbd nodes, e.g. dongle if02)
- Ruling M6: interface fallback stays as conservative as before: iface 00 mouse → "mouse"; ifNN keyboard → "ifNN"; iface 01 non-kbd → "if01_event"; any other non-kbd node → None. — why: never grab nodes we did not grab before without hardware evidence — cost if wrong: an unusual mouse needs a by-id link to be seen.
- Task 5: minor (deferred): button_maps imports private _MOUSE_KEYS/_SIDE_KEYS; NAGA_PIDS duplicated by _NAGA_PID_VALUES; NAGA_DEVICE_SUFFIXES unused
- Task 5: fix round 1/5 (1 addressed, 0 open; commits b7cdc98..a034daa)
- Task 5: complete (commits c52cac4..a034daa, review clean)
- Ruling M7: in T6 ConfigStore keeps its legacy profile API as delegates to for_model("naga_v2_pro") and Settings.active_profile maps to active_profiles["naga_v2_pro"]; migrate_schema_2 wired into daemon+GUI startup now — why: every task must leave a working program — cost if wrong: small cleanup in T7/T12.
- Task 6: dispatched (BASE a034daa, implementer a2d4bc518974cd260, sonnet)
- Task 6: implementer DONE (6bfd04a; 793 passed; old profiles/*.json + mouse-backup.json copied not moved; invalid-model ValueError untranslated)
- Task 6: review Approved (0 Important)
- Task 6: minor (deferred): vacuous test_schema2_runs_at_startup; schema != 2 (e.g. future 3) treated as v1; fixed .tmp name race daemon/GUI (harmless); NAGA_MODEL duplicated in migrate.py; ModelStore.default_profile for unknown model gets Naga bindings; default_profile_for(info) name instead of default_profile(info); ValueError text untranslated (internal).
- Note for T7: daemon/CLI/dbus_service still use root mouse-backup.json — T7 must switch to backups/<model>.json (backup_path).
- Task 6: complete (commits a034daa..6bfd04a, review clean)
- Ruling M8: verified mice (Naga): failed probe only warns (no lock-out of the user's working mouse) and grab stays always-on while connected (today's behaviour); experimental mice: failed probe → read-only, grab on demand. — why: anti-goal "Naga verliert nichts" — cost if wrong: Naga could get writes after a bad probe (same as today).
- Task 7: dispatched (BASE 6bfd04a, implementer a3648d5483df110b1, opus)
- Task 7: implementer DONE_WITH_CONCERNS (9155b4e; 806 passed; concerns: global power settings written to every writable mouse; find_control_hidraw only iface 00; Naga session always exists + NagaService holds Naga profile (interim until T8); unplugged sessions stay listed as disconnected)
- Task 7: review a402844f37681cc5f interrupted by usage limit (resets 09:10 Europe/Berlin) — resume it via SendMessage, then continue the loop.
- Task 7: review Not approved — I1 experimental mouse written without backup when save_backup fails; I2 shutdown write-back ignores _write_ok (re-plugged read-only mouse); I3 global power settings written to every mouse.
- Ruling M9: idle_time / low_battery_threshold (global settings) apply to verified mice only (the Naga) until per-model power settings exist (T12 may move them into DeviceSettings). — why: user configured them for the Naga; persistent writes to unknown mice — cost if wrong: other mice ignore those settings for now.
- Task 7: minor (deferred): learning accepts release/autorepeat (should require value==1); unplugged non-Naga sessions stay in `sessions`; apply_device_settings re-export shim (T8 cleanup); find_control_hidraw only iface 00 (should use info.interface).
- Task 7: fix round 1/5 (3 addressed + optional learning fix, 0 open; commits 9155b4e..4ec67a6)
- Task 7: complete (commits 6bfd04a..4ec67a6, review clean)
- Task 8: dispatched (BASE 4ec67a6, implementer ad3f4cfed3f9a7fd8, sonnet); also carries T7 minors: release resources of unplugged sessions, remove apply_device_settings shim
- Task 8: implementer DONE (d695cee; 841 passed; main_window.py 399/400 lines; non-Naga CLI uses first PID view; hw_status kept for tests)
- Task 8: review Approved (0 Important)
- Task 8: minor (deferred): RestoreOriginal does HID writes synchronously on the event loop (pre-existing); StartLearning not guarded for disconnected/released session (raise NotConnected); redundant lines in tray._add_daemon_entries; first Reload emits ProfileChanged for every model; verify MainWindow handles DaemonError UnknownDevice; CLI non-Naga uses first PID view; dbus-next error names for old interface assumed (fake transport only).
- Task 8: complete (commits 4ec67a6..d695cee, review clean)
- Task 9: dispatched (BASE d695cee, implementer a5ffc3d986ddb2ced, sonnet)
- Task 9: implementer DONE (0abfdf9; 857 passed; generic layout leader lines cross the body (cosmetic); Naga anchors now fractions)
- Task 9: review Not approved — I1 generic.json puts all labels right so leader lines cross the body; I2 no non-crossing test for generic/all layouts; (minor, needed by T11) leader_line top-view entry column only works for right side.
- Task 9: minor (deferred): laserize copies style= attrs; expands shapes inside defs/clipPath/mask; id on bare shape not supported; stroke none/currentColor used as glow colour.
- Task 9: fix round 1/5 (2 addressed + mirrored column + laser hardening, 0 open; commits 0abfdf9..d77c301)
- Task 9: minor (deferred): left-side labels render "Name → Aktion" while right-side render "Name:  Aktion" (check consistency)
- Task 9: complete (commits d695cee..d77c301, review clean)
- Task 10: dispatched (BASE d77c301, implementer af7f918be985e033c, sonnet)
- Task 10: implementer DONE_WITH_CONCERNS (c12dc3c; 891 passed; added entry/via/through routing fields; wheel+left-button lines declared "through" the right button; plate change not pushed to daemon)
- Ruling M10: 6/2-plate key tables = provisional assumption (same codes as keys 1-6 / 1-2 of the 12-plate), marked unverified; user has no plates to test — cost if wrong: user of those plates must use Taste anlernen.
- User request 2026-10-23: OSD on profile/DPI change → plan Task 15, executed before Task 14.
- Task 10: review Approved-with-fix, 1 Important (plate change not sent to daemon → keys 7-12 dead after 6/2→12 until reload)
- Task 10: minor (deferred): ButtonsTab.info fixed to default model (T12 must add set_device); wheel_left/right dots at wheel lower end rather than tilt marks. (Left-button anchor ambiguity included in fix round because the user cares about line placement.)
- Task 10: fix round 1/5 (I1 addressed; minor M left-line clearance judged by controller from screenshot: dot clearly inside left button, line ~10px below right button edge — Ruling: acceptable, revisit after user sees it on 0.3.0 — cost if wrong: one anchor tweak; commits c12dc3c..aa09931)
- Task 10: complete (commits d77c301..aa09931, review clean)
- Ruling M11: plan model names mapped to real DB slugs (death_adder_*, pro_click_v2_vertical_edition); hyper_polling_wireless (dongle) → generic — cost if wrong: one index entry.
- Task 11: dispatched (BASE aa09931, implementer a1ac0df3374652426, sonnet)
- Task 11: implementer DONE_WITH_CONCERNS (fc6c0ce; 1123 passed; abyssus→generic; button sets from memory; ergo DPI pads unlabelled on models without DPI buttons; naga_trinity 7-plate not selectable)
- Task 11: review Not approved — I1 new ButtonIds show raw English ("Taste forward"); I2 abyssus deviates from prefix list.
- Ruling M12: abyssus → generic accepted (Abyssus has no thumb buttons; generic shows fewer wrong elements than ambi) — cost if wrong: one index entry.
- Task 11: minor (deferred unless cheap in fix round): ergo template draws DPI pads on models without DPI buttons; right labels ordered so Rechtsklick is last; naga_trinity 7-plate not selectable; button sets from memory.
- Task 11: fix round 1/5 (I1 addressed, I2 ruled; pads + label order done; commits fc6c0ce..32f839f; controller re-ran suite: 1124 passed)
- Task 11: complete (commits aa09931..32f839f, review clean)
- Task 12: dispatched (BASE 32f839f, implementer a712b1c67fe27042d, opus)
- Task 12: implementer DONE (a78494b; 1158 passed; main_window 362 lines; LearnDialog/ButtonsTab.set_device take ModelStore; on/off-only zones show just "Aus"; MAUSRAD Naga-only until T13)
- Task 12: review Approved (0 Important)
- Task 12: minor (deferred): remap toggle stays visually on after failed save; activate_current silent on failure; macro delete rewrites all profiles of all models; NAGA_MODEL duplicated in device_bar.py; stored poll rate not supported by the mouse not clamped on load; on/off-only lighting zones show just "Aus".
- Task 12: complete (commits 32f839f..a78494b, review clean)
- Task 13: dispatched (BASE a78494b, implementer aa52798a63d832845, sonnet)
- Task 13: implementer DONE (07e17d9; 1168 passed; Profile.to_dict omits empty scroll; scroll_* recorded in _written for R17)
- Task 13: review Approved (0 Important)
- Task 13: minor (deferred): session sends profile.scroll values without own range guard (relies on validate); validate ignores unknown scroll keys; stray blank line device_bar.py:194; "Links"/"Rechts" zone labels lack translator context.
- Task 13: complete (commits a78494b..07e17d9, review clean)
- Task 15: dispatched (BASE 07e17d9, implementer a87e35824c87b06e3, sonnet)
- Task 15: implementer DONE (e41a37d; 1183 passed; profile switch with different DPI shows profile OSD then immediately DPI OSD replacing it)
- Task 15: review Not approved — C1 first profileChanged per model (refresh/reconnect/second device) shows a spurious profile OSD; I1 DPI OSD replaces profile OSD at once; I2 Reload/language change can trigger DPI OSD; I3 no test for device switch.
- Ruling M13: a profile change shows ONE OSD: profile name big, kind line "PROFIL"; DPI changes of that model within 1.5 s after a profile change or after a GUI-initiated Reload are folded/suppressed (no second OSD); first profileChanged per model is a baseline. — why: user wants to see the profile confirmation — cost if wrong: a DPI change right after a profile switch shows no DPI OSD.
- Task 15: minor (deferred): blocking list_profiles/devices D-Bus calls in OSD slots (guard DaemonError at least); disabling OSD doesn't hide a visible one; test file tidiness.
- Task 15: fix round 1/5 (C1, I1-I3 addressed, 0 open; commits e41a37d..6811a37)
- Task 15: minor (deferred): a DaemonError during OSD seeding leaves that model unseeded (first real switch swallowed); seeding loop aborts for remaining models.
- Task 15: complete (commits 07e17d9..6811a37, review clean)
- Task 14: dispatched (BASE 6811a37, implementer a1b2c4e654255b978, sonnet)
- Task 14: implementer DONE (62b39a3; 1196 passed; built with --nodeps + pip tools because python-build/python-installer are not installed on the system; controller removed that artifact — user builds with makepkg -si)
- Task 14: review Approved (0 Important)
- Task 14: minor (deferred): README "kein Gewähr" → "keine Gewähr"; README:17 unwrapped; no 0.3.0 post_upgrade note; __init__/pyproject description still "(Razer Naga V2 Pro)"; makepkg needs python-build/python-installer (user builds with makepkg -si).
- Task 14: complete (commits 6811a37..62b39a3, review clean)
- All tasks complete. Final whole-branch review next (range fdd18c7..HEAD).
- Final review dispatched (aba8f7d623b6c28f6, opus, range fdd18c7..62b39a3, filtered diff)
- Final review: Ready after fixes — I1 0.2.x reader resets settings after migration; I2 no upgrade hint + wrong remedy text; I3 overlapping _connect leaks (pre-existing); I4 CLI writes without probe/with first PID. Findings list: final-findings.md. One fix wave dispatched.
- Final fix wave a98f838e8801a3d2a interrupted by usage limit (resets 14:10); resume via SendMessage.
- Final fix wave done (62b39a3..8459683; 1212 passed). Residual: a 0.2.x rewrite after migration keeps naga active profile but drops other models active profiles/osd/last_model (acceptable). Scoped re-review next.
- Final fix re-review: I1–I4 + cheap fixes ADDRESSED; new residual: interrupted first migration → rerun path skips profile/backup copy (pre-schema2/ exists before copies finish).
- Final: parked — interrupted-first-migration edge case — Ruling M14: park; controller dry-ran the migration on a copy of the user's real ~/.config/razertux (migrated True, second run False, both profiles + backup byte-identical, active profile kept "test", old files kept) so the user's actual upgrade path is verified; originals stay in profiles/ and pre-schema2/ in any case — cost if wrong: after a crash mid-migration profiles look missing until copied by hand (no data loss).
- Final review complete (Ready after fixes; fixes 62b39a3..8459683 reviewed; 1 parked).
