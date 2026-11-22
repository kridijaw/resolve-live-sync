# resolve-live-sync

Tooling for live recordings: sync phone footage to the master audio and set up the edit in DaVinci Resolve.

Footage, masters and edit copies live next to this repo in `Aufnahmen/` and are not tracked.

## How it works

1. `sync` finds where each clip sits against the master by comparing their audio, and falls back to the recording time in the file name. It writes `tools/sessions/<session>.json` for review.
2. `encode` makes 30 fps edit copies, because the phones record a variable frame rate.
3. `resolve` installs two scripts in Resolve. `Live Sync Setup` builds a project with a synced timeline per session. `Live Sync Trim Audio` removes phone audio that other tracks cover, in a copy of the timeline.

Setup, commands and the session file format: [docs/workflow.md](docs/workflow.md).

## Good to know

- `sync` does not overwrite a session file. Delete the JSON to sync again.
- After changing a clip's `in` or `out`, delete its copy in `Edit/` and run `encode` and `resolve` again.
- `Live Sync Setup` leaves filled timelines alone, so later changes to a session file do not reach them.
- Each clip gets one fixed offset. Clock drift between phone and recorder is not corrected.
