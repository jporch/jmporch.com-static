# jmporch.com

The existing [jmporch.com](https://jmporch.com) portfolio, migrated to Zola with
editable content, the original desktop design, responsive repairs, and vanilla
JavaScript. Images, icons, and the existing Josefin Slab font are served locally.
The published site has no JavaScript package or external font dependencies.

## Preview and build

The project pins **Zola 0.23.6**. No Node.js, npm, or Rust toolchain is required.

```sh
bash scripts/install-zola.sh
.cache/bin/zola serve
```

Open the URL printed by Zola (normally `http://127.0.0.1:1111`). Changes to content,
templates, and styles rebuild automatically. The installer downloads the official
binary to `.cache/bin` and verifies its pinned SHA-256 checksum. You can instead
install the same Zola version yourself and use `zola` in these commands.

```sh
.cache/bin/zola check --skip-external-links
.cache/bin/zola build
python3 scripts/verify_site.py --zola .cache/bin/zola
```

The optional integration checks need Python 3 and use only its standard library.
Build output is in `public/`, which is ignored by Git. That directory can also be
copied to an ordinary static webserver.

## Update the content

| Content | File |
| --- | --- |
| About text | `content/about.md` |
| Work experience | `content/work/*.md` |
| Education | `content/education/*.md` |
| Portfolio links and categories | `data/portfolio.toml` |
| Navigation, contact links, résumé location | `data/navigation.toml` |
| Site title, description, name | `zola.toml` |
| Images, résumé, and other downloads | `static/content/` |

Edit Markdown below the `+++` metadata delimiters. Content-only changes do not
require editing the page templates. The `[extra]` dates field supplies the date
label for work and education entries.

To add a job or education entry, copy a neighboring `.md` file, edit its title,
dates and body, then add its content-relative path to the `extra.entries` list in
that directory's `_index.md`. The list controls display order. These entries have
`render = false` because they are embedded in the homepage; Zola excludes such
pages from normal section page lists, so they are loaded explicitly by path.
Selected raw HTML preserves the original custom list and citation markup.
Links to homepage panels in these embedded files use `<a href="#portfolio">`
because the target exists in the final homepage template, not the Markdown file.

The original portfolio RSS archive is preserved at `static/feed.xml`; it remains
a separate, manually maintained feed. The future blog feed is generated from
Markdown posts automatically.

## Add a blog post

Create `content/blog/my-post.md`:

```markdown
+++
title = "My post"
date = 2026-10-14
description = "A short description for the blog index."
draft = true
+++

Write the post in **Markdown**.
```

Preview unpublished work using `.cache/bin/zola serve --drafts`. Set `draft = false`
or remove that line to publish it on the next deployment. Every post must have a
date. Its filename supplies the slug: the example becomes `/blog/my-post/`.
Keep that slug stable when changing the title to preserve incoming links.

The blog automatically generates individual pages, newest-first listings,
pagination (10 posts per page), older/newer links, and `/blog/atom.xml`.
The sidebar's Blog link appears once there is a published post. No sample posts
are published. `/blog/` currently displays an empty-state message.

For images belonging to a post, use `content/blog/my-post/index.md` with image
files beside it and relative Markdown image references.

## Deployment

The default branch is **main**. `.github/workflows/pages.yml`:

1. Checks and builds pull requests targeting `main` without deploying them.
2. Builds and deploys pushes to `main`, including merged pull requests.
3. Also supports a manual run through the Actions tab.

The workflow uses official GitHub Actions pinned to immutable commits, installs
the checksum-verified Zola version, validates generated local links and assets,
and exercises a temporary 12-post blog to check navigation, pagination, feeds,
and draft exclusion. Temporary test posts never enter the deployment artifact.
Only the deployment job receives Pages write and OIDC permissions.

In GitHub **Settings → Pages**, select **GitHub Actions** as the publishing source.
The workflow reads the configured Pages URL and passes it to Zola, so assets work
under the `/jmporch.com-static/` project path and after a future domain change.
No custom domain or DNS changes are part of this migration.

## Design and behavior

`static/style.css` is the original stylesheet. `static/enhancements.css` contains
the local font, accessible button styles, and responsive fixes. Templates retain
the original section IDs, CSS classes, four-panel homepage, and content wrappers;
collapsible headings now contain keyboard-accessible native buttons.

`static/main.js` handles hash navigation, browser back/forward, collapsible entries,
and the mobile menu, including eased opening/closing, breakpoint changes and Escape. Content remains
readable if JavaScript is disabled. Reduced-motion preferences are respected.

The menu no longer hides the content underneath it. Narrow-screen fixes address
heading overflow, the image's doubled padding width, and menu button placement.
The malformed email address was corrected to `porchjm@gmail.com`. The original
wording, résumé, and portfolio destinations are preserved. The footer copyright
year is computed from the current UTC year each time Zola builds the site.

The portrait retains its original attribution. Josefin Slab's license is included
in `static/fonts/OFL.txt`; no new license is assigned to the owner's site content.
