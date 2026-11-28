# Workspace scope and archives

- `archive/` contains local historical deployment bundles and retained release
  artifacts. Ignore it during routine searches, reviews, builds, and tests unless
  the user's task specifically needs historical files, recovery, or comparison.
  Read `archive/README.md` first when that context is needed. Do not recursively
  inventory, validate, or clean the archive as part of ordinary work.
- `backporch-monitor/` is a separate active Git repository. Keep it in place and
  preserve its uncommitted changes. Only inspect or run its checks when the task
  concerns BackPorch; website work does not require its Rust or Node toolchains.
- Start searches in the relevant source directories. Respect `.gitignore` and
  `.rgignore`; avoid workspace-wide `find`, recursive `ls`, `du`, or searches with
  `--no-ignore` unless required. Exclude `archive/`, `.git/`, `.cache/`, `.venv/`,
  `public/`, `target/`, `dist/`, `node_modules/`, and `__pycache__/` from routine
  source inspection. Do not read signing material for unrelated tasks.
- `content/mirror/` and `static/mirror/` are published website content, not
  disposable project leftovers. Keep them in the normal website build.
- `.cache/bin/zola` is the pinned website build tool. Keep installed tools,
  signing material, active worktrees, and running app bundles in place. Generated
  build intermediates may be regenerated; do not rebuild them just to verify
  housekeeping changes.

# Change and deployment process

## Default: local review

- Keep changes local unless the user explicitly requests a commit or deployment.
  Do not commit, push, merge, or trigger a remote deployment by default.
- Run the checks appropriate to the change.
- For website changes, start or reuse the local preview using `scripts/site.py
  serve` with the pinned Zola version. Verify it is reachable and provide a link
  to the relevant page or section so the user can review it.
- Leave the preview running for local review. The usual URL is
  `http://127.0.0.1:1111/`; report the actual URL if it differs.

## "Commit": publish to staging

- An explicit request to commit authorizes committing and pushing the requested
  changes to staging, not production. Do not ask for an additional confirmation.
- Use a feature branch named `codex/<description>` to track the changes, then
  merge them into `staging` and push. A push to `staging` automatically deploys
  GitHub Pages. Keep `main` unchanged.
- Do not start a local preview for this mode. If the project's preview is running,
  stop it gracefully. Do not stop unrelated processes or previews.
- Verify the staging workflow result and provide the staging URL.

## "Deploy": publish directly to production

- An explicit request to deploy authorizes a production deployment without a
  separate local or staging review. Do not ask for an additional confirmation.
- Still create a feature branch named `codex/<description>` and commit the
  requested changes there for tracking. After appropriate automated checks,
  merge it directly into `main` and push. No staging merge or manual release is
  required. A push to `main` automatically deploys production.
- Do not start a preview solely for this mode. Verify the production workflow
  result and, where applicable, the public deployment marker or changed page.

## Scope and branch handling

- Preserve unrelated local changes. Include only changes covered by the user's
  request; never sweep unrelated pending work into a commit or deployment.
- Base feature branches on current `main` unless the requested work requires a
  different base. Keep experimental staging-only changes out of production;
  merge the intended feature branch rather than merging all of `staging` into
  `main`.
- Report what was published, the destination, and any failed or incomplete checks.

## Commands and destinations

- Build and preview through `scripts/site.py` so the portfolio feed is generated.
- See README.md for the pinned Zola installer and validation commands.
- Staging: https://jporch.github.io/jmporch.com-static/
- Production: https://jmporch.com/
- Production deploys versioned releases under `/srv/www/main/releases` on
  `23.92.22.231`, activated through `/srv/www/main/current`.
- The old server is no longer a deployment target. Haywire is served independently
  from `/srv/www/haywire/current`.
