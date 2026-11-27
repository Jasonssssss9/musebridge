# MuseBridge

MuseBridge is a museum guide designed to let visitors scan an artwork's QR code, ask questions on a mobile web page, and explore answers grounded in museum sources with traceable citations.

The [Getty gallery](docs/getty-gallery.md) features 20 illustrated works. Each has a QR code, excerpts from the Getty record and two original MuseBridge viewing notes. Visitors can read the cited passage alongside an answer. The Chicago demo includes two Art Institute of Chicago works. The public Go API manages each artwork conversation. In controlled Agent mode it calls a Python LangGraph workflow that can search museum evidence and use a pinned Qwen model to answer in English, Spanish, Simplified Chinese or Korean. The default Compose setup serves the fixture demo. See the [public Agent setup](docs/public-agent-integration.md) and [M2 verification record](docs/m2-verification.md) for the tested environments and current acceptance status.

| Contributor | Responsibility |
| --- | --- |
| Jeremiah Lim | Mobile frontend and visitor experience |
| Jinqing Zhang | Go backend and system integration |
| Jason Zichen He | Source data, retrieval and evaluation |

See the [documentation](docs/README.md) for setup, design, interfaces, implementation planning and evaluation, and the [web app guide](web/README.md) for running the frontend.
