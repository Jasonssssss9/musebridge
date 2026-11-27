# The Getty gallery

Scan a card at `/qr` or choose a work on the home page. The artwork page opens with a credited, zoomable image. Ask one of the suggested questions or write your own. Open **Sources** under an answer to read the passage and see who wrote it.

The [gallery release](../knowledge/data/getty-gallery-v2.json) contains 20 Getty collection objects spanning painting, photography, sculpture and decorative arts. Each work has three suggested questions, Getty catalog details and description excerpts, and two original MuseBridge viewing notes. Inclusion in the guide does not imply that an object is currently on view.

## Questions to try

| Work | Visitor question | What to look for in the answer |
| --- | --- | --- |
| [Irises](https://www.getty.edu/art/collection/object/103JNH) | Where did Van Gogh begin painting these irises? | The Getty passage identifies the asylum garden in Saint-Rémy. |
| [Starry Night](https://www.getty.edu/art/collection/object/103RGH) | What might the shape on the fence represent? | Getty presents the shadow of two lovers as a possibility. |
| [The Vexed Man](https://www.getty.edu/art/collection/object/103QTJ) | Where should I look before putting a name to this expression? | A MuseBridge viewing note suggests studying particular features. |
| [J.F.W. Herschel](https://www.getty.edu/art/collection/object/104G0S) | Where and when did Cameron first meet Herschel? | Getty places their first meeting in South Africa in 1835. |
| [Cabinet on Stand](https://www.getty.edu/art/collection/object/103QSK) | Which animals appear on the central door, and what do they represent? | Getty identifies the animals and explains their significance. |
| [Pilgrim Flask](https://www.getty.edu/art/collection/object/103RR6) | What should I look for in the blue pattern and the side loops? | A viewing note guides the eye around the object. |

## Run locally

Install frontend dependencies with `npm ci --prefix web`. Use the project's Go and Node toolchains and install the [Agent](../agent/requirements.txt) and [retrieval](../knowledge/requirements.txt) Python dependencies. The gallery uses the pinned Qwen tokenizer and a running Ollama endpoint. Hybrid retrieval needs server-side OpenAI credentials to build a passage index and embed new queries.

```bash
export AGENT_TOKENIZER_PATH=/path/to/qwen35-tokenizer.json
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OPENAI_API_KEY_FILE="$HOME/.config/musebridge/openai.key"
./scripts/run-gallery.sh
```

The default gallery is `getty-gallery-v2`, served at `http://localhost:8080/`, with QR cards at `/qr`. Set `AGENT_PYTHON` and `RETRIEVAL_PYTHON` if the services use different virtual environments. The private endpoint at `http://127.0.0.1:8002/readyz` reports model availability. Start the model and its tunnel before the gallery. Use Ctrl+C to stop the local services.

For a phone, set `PUBLIC_BASE_URL` to the origin the phone will reach and `WEB_HOST=0.0.0.0` before startup. Open the QR page at that same origin. Set `WEB_PORT` to the WSL listening port if Windows forwards a different external port. A phone reading a QR code containing `localhost` will try to open its own device.

## Read the evidence

The release contains 96 Getty passages or catalog values and 40 MuseBridge notes. Source links, text licenses and image credits travel with the record.

- **Getty Museum record.** Catalog values and excerpts have separate locators and rights. Direct excerpts preserve the speaker and qualifications in the record. Some are artists' words quoted by the museum.
- **MuseBridge viewing guide.** Each work has two distinct ways to look. A note might invite someone to follow lines through a room, compare reflections in marble and fabric, or notice how light reveals the surface of a photograph. These are suggestions signed by MuseBridge. They are stored separately from Getty text and link to the relevant object record.
- **Images.** Nineteen local JPEGs come from Getty Open Content. For [Portrait of Mai](https://www.getty.edu/art/collection/object/11409C), the gallery uses a [public-domain reproduction from Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Joshua_Reynolds_-_Portrait_of_Omai.jpg). The image credit links to its source. The catalog and curatorial evidence for Mai still come from Getty.

## Verify a release

Run these commands against the same origin used to start the public server. For the browser check, install Playwright, Pillow and zxing-cpp in a QA environment, plus Playwright Chromium or an existing Chrome installation.

```bash
python -m acceptance.gallery_browser http://localhost:8080 knowledge/data/getty-gallery-v2.json
python -m agent.testing.gallery_smoke http://localhost:8080 --report /tmp/getty-published.jsonl
python -m agent.testing.gallery_smoke http://localhost:8080 --suggestion 3 --report /tmp/getty-third.jsonl
python -m agent.testing.gallery_smoke http://localhost:8080 --cases agent/testing/getty_gallery_development_cases.json --report /tmp/getty-development.jsonl
```

The browser check decodes rendered QR pixels and opens every destination at desktop and phone sizes. It checks each JPEG against the release file, including dimensions, then opens the image dialog and checks the artwork details and suggested questions. The live checks open fresh conversations through Go. The default run asks the first two questions on each page, one museum question and one viewing question. The third-question run covers the remaining 20 buttons. The development cases add English, Spanish, Chinese and Korean questions, including a seasonal word shared with another title, two close-looking exercises and a question about a precise duration missing from the sources. Use a fresh report path for each run. Read the answer alongside the cited passage to check meaning and attribution. The automated checks cover response status, output language field, artwork scope, source type and any specified passage IDs.

If a Windows hotspot forwards to a different WSL port, pass `--connect-url http://127.0.0.1:<web-port>` to the live suites while keeping the phone-facing origin as their first argument.

## Recorded gallery check

The [public Qwen results](../eval/reports/getty-gallery-v2.qwen.json) pair each visitor question with the actual model answer and its citation IDs. Resolve each ID in the [versioned seed](../knowledge/data/getty-gallery-v2.json) to see exactly what the visitor could read. Earlier development outputs are identified separately.

The frozen release passed the browser checks for all 20 works in each viewport. The real model run used Qwen 3.5 9B with hybrid retrieval and covered all 60 published buttons plus 13 development questions across four languages. All 73 passed the public response and citation checks. In a separate assistant review, 70 answered the question without a material factual error. In 65 cases every added detail and qualification also had support in the displayed citations. These figures describe this development run. The report records the exceptions.

In the delivery run, Cameron and Herschel's first meeting was correctly placed in South Africa in 1835. The cabinet answer distinguished the French cockerel from the eagle and lion it stands over. Munch's fence remained a possible shadow of two lovers. A harder example is Monet's wheatstacks. Getty says that by the following summer Monet had painted them at least thirty times. In one answer Qwen treated that eventual count as a goal he had set in advance. The returned passage lets the visitor check the distinction.

## Extend the collection

1. Copy the current seed to a new versioned file, such as `knowledge/data/getty-gallery-v3.json`. Update the top-level version and each artwork's version. Add the object in gallery order, with its canonical Getty record, credit, descriptive alt text and suggested questions.
2. Verify the rights of the specific image. Store a JPEG at `web/public/artworks/<slug>.jpg` and keep its original aspect ratio. Record its source URL and license separately from the text rights.
3. Add the Getty record and the MuseBridge guide to `sources`. Link the guide to its museum source with `based_on_source_id`. In `chunks`, give catalog values, quoted excerpts and original notes distinct IDs, locators and rights. Keep the first two suggested questions in museum and viewing-guide order.
4. Calculate the companion SHA-256 digest and start the new release. The home page, QR page and retrieval scope follow the dataset. Each release has its own vector cache.

```bash
version=getty-gallery-v3
sha256sum "knowledge/data/$version.json" | cut -d' ' -f1 > "knowledge/data/$version.sha256"
DATASET_VERSION="$version" ./scripts/run-gallery.sh
```

## Three-work pilot

Use `DATASET_VERSION=getty-pilot-v1 ./scripts/run-gallery.sh` to reproduce the original Monet, Munch and Ter Brugghen pilot. In that release, six English questions returned the expected artwork and source type through the public Go endpoint with real Qwen and hybrid retrieval. A two-turn Munch question switched from a MuseBridge note to Getty's interpretation. A physical iPhone opened the Monet page on a Windows hotspot.
