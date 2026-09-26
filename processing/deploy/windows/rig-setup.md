# Rig PC setup (SPEC §4.1, §4.2)

Each telescope's rig PC runs only NINA. Altair runs on the processing PC and
reaches the rig through a network share of NINA's save folder.

1. **Save locally.** In NINA, save images to a folder on the rig PC's own disk,
   for example `D:\NINA`, with the file pattern from SPEC §4.2:
   `$$DATEMINUS12$$\$$TARGETNAME$$\$$IMAGETYPE$$\$$FILTER$$\$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$`
2. **Share the folder** (e.g. `\\rig-esprit\NINA`) with one dedicated Windows account
   that the processing PC uses. Give it **Modify** (read, plus delete for cleanup). If
   you'd rather Altair never deletes on the rig, give **Read** and set
   `rigs.<name>.cleanup.enabled: false`; `altair storage cleanup --dry-run` then lists
   what is safe to delete by hand.
3. **Credentials on the processing PC:** store the share account in Windows Credential
   Manager under the rig's `credential_target` (e.g. `altair-rig-esprit`), then
   `altair rigs check --rig esprit`.
4. **Session end.**
   - With a Hub: NINA's end-of-sequence External Script runs `robs end-of-night`
     (the rig agent). Nothing from Altair is installed on the rig.
   - Standalone: copy `altair-session-end.cmd` to the rig (e.g. `C:\Tools`) and add it
     as an External Script in the sequence's *End* area, with the NINA folder as its
     argument. If it doesn't run, the night still closes by quiescence after dawn, or
     by the daily scheduled fallback.
5. **Keep it awake** and give it disk for about a week of nights: until the processing
   PC collects a frame, the rig holds the only copy. Afterwards it is a 3-day buffer.
