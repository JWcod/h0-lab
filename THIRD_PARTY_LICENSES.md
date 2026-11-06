# Third-party licenses

H0 Lab's own code is under the Apache License 2.0 (see [LICENSE](LICENSE)). This file lists the third-party software it
depends on. The table is generated from the metadata of the installed packages pinned in `requirements.lock.txt`; each
package's own license text ships inside its distribution and is the authoritative version.

## Bundled in this repository

| Component | License | Where |
|---|---|---|
| Apache ECharts 5.6.0 | Apache-2.0 | `web/vendor/echarts-5.6.0.min.js`, license and notices in `web/vendor/ECHARTS_LICENSE.txt` |

## Python dependencies (installed by `install.sh`, not stored in this repository)

| Package | Version | License | Project page |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| beautifulsoup4 | 4.15.0 | MIT | https://www.crummy.com/software/BeautifulSoup/bs4/ |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| charset-normalizer | 3.5.2 | MIT | https://charset-normalizer.readthedocs.io/ |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| curl_cffi | 0.16.3 | MIT | https://github.com/lexiforest/curl_cffi |
| fastapi | 0.143.1 | MIT | https://github.com/fastapi/fastapi |
| feedparser | 6.0.14 | BSD-2-Clause | https://github.com/kurtmckee/feedparser |
| feedparser-sgmllib | 2.1.0 | PSF-2.0 | https://github.com/python-syndication/feedparser-sgmllib |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httptools | 0.9.0 | MIT | https://github.com/MagicStack/httptools |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| lxml | 6.1.3 | BSD-3-Clause | https://lxml.de/ |
| multitasking | 0.0.13 | Apache-2.0 | https://github.com/ranaroussi/multitasking |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | https://numpy.org |
| opentelemetry-api | 1.45.1 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| pandas | 3.0.6 | BSD-3-Clause | https://pandas.pydata.org |
| peewee | 4.5.3 | MIT | https://github.com/coleifer/peewee |
| platformdirs | 4.13.0 | MIT | https://github.com/tox-dev/platformdirs |
| protobuf | 7.36.2 | BSD-3-Clause | https://developers.google.com/protocol-buffers/ |
| pycparser | 3.11 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.1 | MIT | https://github.com/pydantic/pydantic |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause | https://github.com/dateutil/dateutil |
| python-dotenv | 1.2.4 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| pytz | 2026.5 | MIT | http://pythonhosted.org/pytz |
| PyYAML | 6.0.3 | MIT | https://pyyaml.org/ |
| requests | 2.34.2 | Apache-2.0 | https://github.com/psf/requests |
| scipy | 1.18.1 | BSD-3-Clause | https://scipy.org/ |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six |
| soupsieve | 3.0.3 | MIT | https://github.com/facelessuser/soupsieve |
| starlette | 1.8.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| urllib3 | 2.8.0 | MIT | https://urllib3.readthedocs.io |
| uvicorn | 0.54.0 | BSD-3-Clause | https://uvicorn.dev/ |
| uvloop | 0.23.0 | MIT OR Apache-2.0 | https://github.com/MagicStack/uvloop |
| watchfiles | 1.3.0 | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.2 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| yfinance | 1.7.0 | Apache-2.0 | https://github.com/ranaroussi/yfinance |

Notes:

- **certifi** is under the Mozilla Public License 2.0, a file-level copyleft license. H0 Lab does not modify it. Its
  source is available from the project page above.
- No dependency in this list is under the GPL or AGPL at the time this file was generated.

## macOS application bundles

A macOS `.app` built with PyInstaller also contains a copy of the Python runtime (Python Software Foundation License)
and binary libraries pulled in by the dependencies above, such as OpenSSL (Apache-2.0), libcurl (curl license),
SQLite (public domain), and the NumPy and SciPy native libraries (BSD-style licenses, with OpenBLAS under BSD-3-Clause
and the GCC runtime under GPL with the runtime library exception). PyInstaller's own license (GPL-2.0 with a bootloader
exception) allows distributing bundled applications under other terms. Anyone redistributing a bundle must keep these
notices. Release builds should include this file and the license files collected from the build environment.
