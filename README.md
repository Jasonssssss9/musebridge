# MuseBridge

MuseBridge is a museum guide designed to let visitors scan an artwork's QR code, ask questions on a mobile web page, and explore answers grounded in museum sources with traceable citations.

The project combines a mobile frontend, a Go application backend, and a Python retrieval service. The local prototype focuses on two Art Institute of Chicago works. It includes a versioned source dataset, artwork and conversation APIs, a validated retrieval and fixture-answer flow, and a mobile web app with mock and live modes. Stage-one answers use clearly labeled, reviewed test responses backed by museum evidence.

| Contributor | Responsibility |
| --- | --- |
| Jeremiah Lim | Mobile frontend and visitor experience |
| Jinqing Zhang | Go backend and system integration |
| Jason Zichen He | Source data, retrieval and evaluation |

See the [documentation](docs/README.md) for setup, design, interfaces, implementation planning and evaluation, and the [web app guide](web/README.md) for running the frontend.
