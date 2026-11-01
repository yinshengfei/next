# Third-party licenses

The project's PolyForm license and any separate commercial agreement apply
to project-owned material only. Dependencies keep their own licenses.
No vendored dependency source was identified in this repository's tracked
application, tests, or build scripts during the licensing review.

## Direct dependencies

These versions and license identifiers were checked against the installed
package metadata and requirements during the review on 2026-11-01.

| Package | Version | License |
| --- | --- | --- |
| FastAPI | 0.128.8 | MIT |
| Uvicorn | 0.39.0 | BSD-3-Clause |
| HTTPX | 0.28.1 | BSD-3-Clause |
| OpenAI Python SDK | 2.48.0 | Apache-2.0 |
| keyring | 25.7.0 | MIT |
| Pydantic | 2.13.5 | MIT |
| pytest (development) | 8.4.2 installed; requirements allow >=8,<9 | MIT |

## Transitive dependencies and distributions

The installed runtime/development environment also includes dependencies
under MIT, BSD, Apache-2.0, PSF-2.0, and MPL-2.0 terms. In particular, certifi
reports MPL-2.0 and tqdm reports MPL-2.0 AND MIT. These licenses do not
relicense independent Next source files, but their own distribution and
source-availability obligations still apply to the covered material.

This inventory is not a complete set of third-party license texts and does
not replace those texts. Recheck the resolved dependency tree for each
release. The current macOS launcher refers to an existing local Python
installation/environment; it does not bundle that environment. If you later
ship a bundled app, wheel collection, container, or installer containing
dependencies, include their applicable copyright notices and license texts,
comply with Apache NOTICE requirements where applicable, and provide any
source availability required for MPL-covered files. Do not apply PolyForm
restrictions to those dependencies.

Todoist and AI providers are external services with separate terms; this
repository license does not grant service access or trademark rights.
