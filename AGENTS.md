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
- Production synchronizes directly into `/srv/jmporch-deploy/releases/current`.
