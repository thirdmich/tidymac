# tidymac safety policy

## Protection tiers

**Tier 1 — system protected (never suggested, never deleted, no exceptions)**
- OS paths: `/System`, `/usr`, `/private`, `/etc`, `/var`, `/Applications`
- App bundles (anything inside `*.app/`), kernel extensions
- Photo & video library bundles: `*.photoslibrary` (Apple Photos), `*.imovielibrary`, `*.fcpbundle` (raw filesystem edits inside these corrupt the internal SQLite database and iCloud sync)
- `~/Library`: Keychains, Preferences, Saved Application State, Cookies, Mail,
  Messages, Safari, Fonts, Containers, Group Containers, CloudStorage,
  MobileSync/Backup (iPhone/iPad backups)
- Active runtimes: `vm_bundles` (VM images/session data), `~/.docker`, `~/.lima`
- Package manager stores (CLI-cleaned only): `~/.npm/_cacache`, `~/.pnpm-store`,
  `~/Library/pnpm/store`, `~/.yarn/cache`, `~/go/pkg/mod`
- `~/.Trash` (the user empties Trash themselves), `~/.ssh`, `~/.gnupg`
- Any `.plist` file, any `.DS_Store`, any path containing an email address

**Tier 2 — user data (never auto-suggested; removable only on explicit user action)**
- `~/Documents`, `~/Desktop`, `~/Pictures`, `~/Music`, `~/Movies`, `~/Downloads`
- Standalone photos, videos, and screenshots outside of Apple Photos are managed via `tidymac media` or explicit AI commands.
- Pointing a command at one of these directories, or selecting the
  "Old Downloads" category, counts as the explicit flag.
- In backup mode, user data is always copied to the drive first regardless of size.


Files inside dependency dirs (`node_modules`, `.venv`, `.git`, `Pods`, ...) are
never suggested individually; stale projects' `node_modules` are offered as
whole directories via the clutter scan.

When unsure, skip: prefer a missed cleanup over a bad deletion.

## Package manager caches

Cleaned via their own CLI so store indexes stay consistent — never by deleting files:

| Store | Command |
|---|---|
| npm | `npm cache clean --force` |
| pnpm | `pnpm store prune` |
| yarn | `yarn cache clean` |
| Go modules | `go clean -modcache` |

## External drive modes

External drives are **write-only targets**: tidymac never deletes, formats, or
reorganizes anything already on a drive.

**Archive offload (`tidymac offload`)** — for files worth keeping:
- Copies to `/Archive/{YYYY-MM-DD}/` preserving folder structure
- Skips files currently open (checked with `lsof`)
- Verifies the copy (size; xxhash checksum for files ≥ 1 GB) before removing the original
- Leaves a `<name>.README.txt` breadcrumb at the original location

**Backup before delete (`--backup` on any cleanup command)**:
- Files ≥ 100 MB (and all user data, any size): copy to `/Backup/{YYYY-MM-DD}/`,
  verify, then delete from the Mac
- Smaller non-user-data files: deleted directly

**Drive handling**:
- Time Machine and read-only volumes are excluded from detection
- Drives without `size × 1.1` free space are skipped
- Drive name, free space, and estimated post-operation space are shown before proceeding
- Warns on fsck recovery folders (signs of a previous unsafe eject)
- If the drive disconnects mid-operation, the run pauses and resumes only
  after the drive is back
- Safe eject is offered after every drive operation

---

Made by [FusionICT](https://fusionict.com)
