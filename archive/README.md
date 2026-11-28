# Local archive

Historical artifacts moved here on November 26, 2026. Skip this directory for
routine website and BackPorch work. Its contents are local and ignored by Git,
apart from this inventory; moving a file here does not create a remote backup.

| Location | Previous location | Contents |
| --- | --- | --- |
| `deployment-bundles/backporch-build/` | `.cache/backporch-build/` | Earlier backend and desktop source tarballs. |
| `deployment-bundles/minecraft-deploy/` | `.cache/minecraft-deploy/` | Initial installer bundle, source commit, and checksums. |
| `deployment-bundles/minecraft-upgrade/` | `.cache/minecraft-upgrade/` | Previous manager upgrade bundle and checksums. |
| `deployment-bundles/valheim-setup/` | `.cache/valheim-setup/` | Previous setup bundles and source manifests. |
| `deployment-bundles/valheim-readiness-fix/` | `.cache/valheim-readiness-fix/` | One-time readiness patch bundle. |
| `backporch-monitor/dist/` | `backporch-monitor/dist/` | Packaged Windows release files retained for reference or redistribution. |

The active `backporch-monitor/` repository stays at the workspace root with its
Git history and uncommitted work intact. Disposable Rust build intermediates,
the generated client web bundle, and completed build/check logs were removed.
The running Mac application under `backporch-monitor/target/release/bundle/`
was retained, along with installed dependencies, cached toolchains, signing
material, deployment records, and linked worktrees. The next build regenerates
intermediates and may take longer after this cleanup.

To recover a deployment bundle or release package, copy the relevant directory
back to its previous location without overwriting newer work. Source remains
in the active repositories. The published Portfolio Mirror under
`content/mirror/` and `static/mirror/` was left in place.
