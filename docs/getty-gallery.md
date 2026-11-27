# Getty gallery pilot

The gallery opens each artwork by QR code or from the home page. Visitors can enlarge a local image, ask a question and expand the sources under the answer. The first suggested question asks about a museum passage. The second invites a closer look using a MuseBridge guide.

| Work | Getty collection record | A question to try |
| --- | --- | --- |
| Claude Monet, *The Portal of Rouen Cathedral in Morning Light* | [108HGY](https://www.getty.edu/art/collection/object/108HGY) | Where did Monet set up to paint this view of the cathedral? |
| Edvard Munch, *Starry Night* | [103RGH](https://www.getty.edu/art/collection/object/103RGH) | What might the shape on the fence represent? |
| Hendrick ter Brugghen, *Bacchante with an Ape* | [103RFP](https://www.getty.edu/art/collection/object/103RFP) | How does the ape imitate the woman? |

## Start the gallery

Use the project's Go and Node toolchains, install frontend dependencies with `npm ci --prefix web`, and supply Python with the [Agent](../agent/requirements.txt) and [retrieval](../knowledge/requirements.txt) dependencies. The server needs the pinned Qwen tokenizer and access to the project's Ollama model. Hybrid retrieval uses a server-side OpenAI key for new queries and the first passage index build.

```bash
export AGENT_TOKENIZER_PATH=/path/to/qwen35-tokenizer.json
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OPENAI_API_KEY_FILE="$HOME/.config/musebridge/openai.key"
./scripts/run-gallery.sh
```

Set `AGENT_PYTHON` and `RETRIEVAL_PYTHON` if the two services use different virtual environments. By default the browser opens at `http://localhost:8080` and printable codes are at `/qr`. The private Agent readiness check at `http://127.0.0.1:8002/readyz` reports whether real Qwen is ready. Start Ollama and its tunnel before launching the gallery. If Ollama was unavailable at startup, restart the gallery after restoring the connection. Ctrl+C stops the local gallery services.

For a phone, open the QR page using the same reachable origin the phone uses. Set `PUBLIC_BASE_URL` to that origin and `WEB_HOST=0.0.0.0` before startup. Set `WEB_PORT` to the listening port on WSL if Windows forwards a different external port. The model and retrieval services stay on loopback.

## Source attribution

The release contains 15 Getty passages or catalog values and three original viewing notes. A `museum_record` citation shows the museum excerpt and its locator. A `guide_note` citation credits MuseBridge and links to the underlying Getty record. A viewing prompt can invite a visitor to trace a line or compare two shapes. Claims about the artist or the museum's interpretation need museum evidence. Keep qualifications such as “may be a shadow” when discussing Munch's fence. Image rights and text rights are stored separately.

## Add another work

1. Create a versioned copy of [the seed](../knowledge/data/getty-pilot-v1.json) in `knowledge/data/`. Set the new dataset version at the top level and on each artwork. Append the new object to `artworks` in gallery order, including its canonical Getty ID, catalog URL, image credit and suggestions.
2. Add the verified Getty JPEG as `web/public/artworks/<slug>.jpg`. Store its IIIF URL, rights and descriptive alt text on the artwork. Keep the aspect ratio of the original.
3. Add the museum record and signed MuseBridge guide to `sources`. Set `based_on_source_id` on the guide. Add excerpts to `chunks` with unique IDs, topics, exact locators, attribution and rights. Use the first two suggested questions for a museum fact and a viewing prompt, respectively.
4. Generate the companion digest from the exact JSON bytes and start the gallery with the new version. New works then appear in the gallery, QR cards and search scope. Each release has its own vector cache.

```bash
version=getty-gallery-v2
sha256sum "knowledge/data/$version.json" | cut -d' ' -f1 > "knowledge/data/$version.sha256"
DATASET_VERSION="$version" ./scripts/run-gallery.sh
```

## Exercise the public workflow

From another terminal, use the exact browser origin configured for Go.

```bash
python -m agent.testing.gallery_smoke http://localhost:8080
```

When the browser origin goes through a Windows hotspot forward, add `--connect-url http://127.0.0.1:8082` to send requests through the local Vite port while preserving the public Origin header.

The smoke run opens fresh conversations and prints each actual answer and its citation IDs. It checks the artwork, source kind, language and public response status. For the wording review, check that the museum examples identify the dressmaker's shop, qualify the possible shadow, and describe the ape's smaller bunch of grapes. The viewing prompts should credit MuseBridge and offer a concrete way to look at the picture.

## Pilot verification, November 26, 2026

The reviewed `getty-pilot-v1` release contains three works and 18 passages. The real run used the public Go conversation endpoint, OpenAI hybrid retrieval and `qwen3.5:9b-q4_K_M` in Ollama. Each of the six English questions returned `answered` with a citation from the correct artwork and source type.

| Work | Museum question and observed answer | MuseBridge viewing suggestion |
| --- | --- | --- |
| Monet | He worked in the front room of a dressmaker’s shop across from the cathedral. | Start at the dark doorway and trace a carved edge upward. |
| Munch | The shape on the fence **may** be the shadow of two lovers. | Follow the pale fence toward the trees, then trace the shoreline. |
| Ter Brugghen | The ape holds a smaller bunch of grapes in its right paw, echoing the woman’s gesture. | Look back and forth between the two bunches of grapes. |

In a separate two-turn Munch conversation, Qwen switched from the MuseBridge suggestion to the Getty passage when the visitor asked what the museum said about the fence. Both responses carried the correct attribution. All three QR codes were decoded from rendered browser images and opened the corresponding artwork on mobile and desktop viewports. A physical iPhone on the Windows hotspot fetched the Monet catalog record and JPEG and opened a conversation. These are English pilot checks for this release.
