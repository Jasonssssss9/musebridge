# Evidence for Talk with Vincent

The [Vincent reference pack](../knowledge/data/reference-packs/vincent-irises-v1.json) adds four English excerpts for Getty’s *Irises* (1889, accession 90.PA.20). Its [SHA-256 file](../knowledge/data/reference-packs/vincent-irises-v1.sha256) identifies the exact bytes. The [20-work gallery](../knowledge/data/getty-gallery-v2.json) still supplies the catalog facts, museum description and looking exercises. Both sets of evidence are available to the designer of the Vincent workflow; the role pack is a data handoff for that implementation.

## What the new passages establish

| Evidence ID | Speaker, date and source | Supported reading |
| --- | --- | --- |
| `vincent-772-garden-colors` | Vincent, writing at Saint-Rémy on 9 May 1889. In a letter with separate sections for Theo and Jo, this passage occurs in Theo’s section. Editorial note 4 identifies the Getty painting, F 608 / JH 1691. [Letter 772](https://vangoghletters.org/vg/letters/let772/letter.html) | He described the irises as violet and named a lilac bush as another garden subject he was working on. First-person paraphrase is appropriate. This letter establishes his wording, not a chemical analysis. |
| `vincent-789-studies-after-nature` | Vincent to Theo, dated by the editors to 14 **or** 15 July 1889. The letter introduces a group of seven studies and lists *Irises* first. Note 9 confirms the same work. [Letter 789](https://vangoghletters.org/vg/letters/let789/letter.html) | Vincent described this group as studies made from nature and wrote that he was enclosing them. He did not say that the white flower symbolized something or that he painted seven versions of *Irises*. |
| `vincent-getty-light-change` | Getty’s *Ultra-Violet* exhibition record, in its Museum Collection database. [Exhibition record](https://www.getty.edu/art/collection/exhibition/16D4X1) | Getty says light permanently changed some colors in the painting. This statement alone identifies neither a particular pigment nor the original shade of every petal. |
| `vincent-getty-faded-red` | Getty paintings conservator Devi Ormond in conversation with Beth Harris. The relevant passage is in Ormond’s first response. [Published transcript](https://smarthistory.org/vincent-van-gogh-irises-the-search-for-violet-getty-conversations/) · [Alternate transcript](https://www.khanacademy.org/humanities/becoming-modern/avant-garde-france/post-impressionism/v/van-gogh-irises-part-2) | Ormond describes fading of the red pigment geranium lake, which was mixed with blues to make violet. Credit this explanation to the modern research. A claim that all red vanished from every flower would exceed the passage. |

## Visitor questions and speaking boundaries

These are development cases for C. The suggested directions describe what a good answer would do. The model writes the actual words and memory in the next phase.

| Visitor question | Expected evidence | What the role can say or remember |
| --- | --- | --- |
| “Vincent, what color did you call the irises?” | `vincent-772-garden-colors` | Recall describing them as violet when writing to Theo. If quoting, preserve the wording of the credited English edition. |
| “Did you find anything else to paint there?” after the color question | `vincent-772-garden-colors` | The other subject in that passage was a lilac bush from the garden. Keep it distinct from the flowers on this canvas. |
| “Why do they look blue to me now?” after the color question | `vincent-getty-faded-red`, `vincent-getty-light-change` | Resolve *they* to the irises. Explain that the red component of the violet mixture faded with exposure to light. Attribute the finding to Getty. Remember the visitor’s observation and the researchers’ explanation separately. |
| “Did you consider this a study?” | `vincent-789-studies-after-nature` | First person can describe including this work among the studies from nature. If asked when the letter was written, preserve the two possible July dates. |
| “What did Theo think of it?” | `getty-103jnh-description-02` | Attribute the reaction and exhibition submission to Theo. Keep his assessment distinct from Vincent’s own description of his work. |
| “Where did you begin working on this?” | `getty-103jnh-description-01`; `vincent-772-garden-colors` for Vincent’s own account | Getty places the work in the garden at Saint-Rémy. The letter confirms the garden subjects. Neither passage specifies the spot where he set his easel. |
| “I noticed the leaves first.” | Direct acknowledgment; `getty-103jnh-guide-02` for the looking exercise | Follow the visitor’s interest. A MuseBridge suggestion can invite them to trace a leaf. It establishes no historical motive for painting it. |
| “Was the white flower meant to look lonely?” | **No evidence for that intention.** `getty-103jnh-guide-01` supports the visual exercise only. | Keep loneliness as the visitor’s interpretation. On “So that’s why you painted it?”, preserve the same uncertainty. |
| “Which exact violet should I mix to recreate the original?” | **No exact recipe.** `vincent-getty-faded-red` supports only the named red and the use of blues in the mixture. | Explain what is supported. Neither proportions nor a precise original hue can be recovered from this passage. |

### How this can sound

These are original examples of role dialogue for review. They are distinct from quotations on the source cards.

- **Vincent’s recollection.** “I described the irises as violet when I wrote to Theo. I was working on a lilac bush from the garden then, too.”
- **A present-day finding.** “They do look bluer now. Getty’s conservators found that the red in the violet paint had faded.”
- **A visitor’s interpretation.** “I can see why that one white flower feels lonely to you. Try covering it for a moment. Where does your eye go?” Memory can retain that the visitor read the bloom as lonely. Its intended symbolism remains unresolved.

## Editions, rights and provenance

The letters use the English edition edited by Leo Jansen, Hans Luijten and Nienke Bakker, published by the Van Gogh Museum and Huygens Institute (2009). The edition identifies the artwork in its editorial notes. Those notes are metadata, not Vincent’s own words. The source locators refer to the publishers’ fixed TEI edition. Note markers were omitted in letter 772; the bracketed ellipsis in letter 789 marks a jump to the work list. The edition’s English translations are licensed **CC BY-NC-SA 4.0**. [Letter 772 XML](https://raw.githubusercontent.com/HuygensING/vangogh/ab158199731faf965b4312ff98815f687b8a6811/tei/2025-03-12/letters/let772.xml) · [Letter 789 XML](https://raw.githubusercontent.com/HuygensING/vangogh/ab158199731faf965b4312ff98815f687b8a6811/tei/2025-03-12/letters/let789.xml)

The Getty exhibition sentence is from the **Museum Collection record**, whose text is covered by **CC BY 4.0** under Getty’s collection terms. The pigment excerpt is from the Getty/Smarthistory conversation, under Smarthistory’s **CC BY-NC-SA 4.0** license. Getty’s separate research page corroborates the result but supplies no excerpt to this pack. These credits refer to the text; the artwork image keeps the gallery’s own credit. [Getty collection terms](https://www.getty.edu/legal/terms-of-use/) · [Smarthistory licensing](https://smarthistory.org/faq) · [Getty conservation research](https://www.getty.edu/projects/van-goghs-irises-a-closer-look/)

Keep the source name, edition, license link, attribution and changes with each excerpt. The CC BY-NC-SA material is included for the noncommercial course project; distributed adaptations of that material carry the same license. Reassess the choice of excerpts before any commercial use. [CC BY-NC-SA 4.0 terms](https://creativecommons.org/licenses/by-nc-sa/4.0/)

For integration, use the scope and validation rules in the [Vincent contract](vincent-contract.md#5-source-identity-and-character-behavior). The focused offline check runs from the repository root with `.venv/bin/python -m unittest agent.tests.test_vincent_reference_pack -v`.
