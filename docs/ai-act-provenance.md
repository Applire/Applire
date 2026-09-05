# AI-Provenance Marking of Generated Documents

**Audience:** self-hosters, contributors, and anyone who needs to verify that a document Applire produced says so.
**Governing decision:** ADR-085 (summarised in [`ARCHITECTURE.md`](./ARCHITECTURE.md)).

Every CV and cover letter Applire renders — PDF **and** `.docx`, every template, both languages, both the
web and the agent (MCP) door — carries **machine-readable metadata saying it was produced by a
generative AI system**. Nothing about the document *looks* different.

## Why

Article 50(2) of the EU AI Act (Regulation (EU) 2024/1689) requires the provider of an AI system that
generates synthetic text to mark its outputs in a machine-readable format so they are detectable as
artificially generated. Three points decide that this lands on Applire rather than on the model:

- Applire integrates a third-party model over an API, so **Applire is the provider of the *system***.
  The Commission Guidelines (`C(2026) 5054 final`, ¶27) only *encourage* model-level marking upstream.
- The open-source exemption in Article 2(12) **explicitly does not cover Article 50** — the AGPL licence
  does not exempt this.
- The "assistive function / standard editing" exception covers grammar, spellcheck and translation. A
  generated cover letter is new prose and a tailored CV is substantial rewriting, so it does not apply.

This is *not* Article 50(4): no visible "AI-generated" banner is required for a job application, and
Applire does not put one on your documents.

If you run Applire yourself, you are the provider of your own instance, and this marking is what
discharges that obligation on your behalf. It works out of the box; there is nothing to configure.

## What is written, and where

### PDF

Two carriers, one claim.

**1. An XMP metadata packet** (the primary, machine-readable claim), referenced from the document
catalog's `/Metadata`:

| Property | Value |
|---|---|
| `Iptc4xmpExt:DigitalSourceType` | `http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia` |
| `applireAI:aiGenerated` | `true` |
| `applireAI:generator` | `Applire` |
| `applireAI:generatorVersion` | the running version |
| `applireAI:generatedAt` | ISO-8601 UTC timestamp of the render |
| `applireAI:markingSpec` | `EU AI Act Art. 50(2)` |
| `xmp:CreatorTool` | `Applire <version>` |

`Iptc4xmpExt` is `http://iptc.org/std/Iptc4xmpExt/2008-02/`; the Applire namespace is
`https://applire.de/ns/ai-provenance/1.0/`. `DigitalSourceType` is the property C2PA and the large
generative services already use, so a third-party tool that understands one understands this.

**2. Duplicate keys in the PDF Info dictionary** — `/AIGenerated`, `/AIGeneratedBy`, `/AIGeneratedAt`,
`/AIDigitalSourceType`. These are a **convenience for a human opening Document
Properties**, not the claim: the PDF specification defines no standard key for this, so anything here is
bespoke. The XMP packet with its documented namespace is what carries the meaning.

### DOCX

The `.docx` export is a second delivered artefact, so it is marked too — in OOXML's own vocabulary,
because the format has no XMP surface:

- **`docProps/custom.xml`** (custom document properties, visible in Word under
  *File → Info → Properties → Advanced*): `AIGenerated`, `AIGeneratedBy`, `AIGeneratedAt`,
  `AIDigitalSourceType`, `AIMarkingSpec` — the same claim as the XMP packet.
- **`docProps/core.xml`**: a human-readable `Comments` line, `Keywords = AI-generated`, and the
  document `Title` (e.g. *Lebenslauf – Anna Bauer*), which follows the document's language exactly as
  the PDF's title does.

## What the mark says — and what it does not

It records the **generation event**: this file was produced by Applire version *V* at time *T*, and its
text is machine-generated.

It deliberately does **not** contain:

- your API key, or any credential;
- the exact model id, or the configured provider name;
- your name, the job you applied for, or any document/user identifier;
- any text from the document itself.

And it does **not** claim that the file still contains exactly what Applire generated. If you edit the
document afterwards — which you are meant to be able to do — the mark still describes the generation
event, not the edited result. A CV in which you overrode a section by hand is marked exactly like one
you did not touch: marking more than strictly necessary is the safe direction here; failing to mark
generated output is the one that is not.

There is one deliberate exception. A document whose content **your own agent supplied verbatim through
the MCP door** (`render_document`, the BYOI path) carries
`DigitalSourceType = compositeWithTrainedAlgorithmicMedia` instead of `trainedAlgorithmicMedia`, on both
the PDF and the `.docx`. Applire rendered that document; it did not write it, and it cannot attest whose
model — or whether a model at all — produced the text. Saying "a trained model made this" would be a
claim about a process Applire never observed, so it says the weaker true thing instead: model-made
content passed through this renderer. The value comes from the stored document's own origin, so it is
the same on the first download and on every later one.

## What it does not survive — measured, not assumed

The mark lives in a metadata layer. It survives copying, e-mailing and archiving. It does **not**
survive a downstream party re-rendering the file.

We measured the common case rather than guessing: running a marked PDF through **Ghostscript 10.07.0**
(`-sDEVICE=pdfwrite`, the engine behind many "compress PDF" and print-to-PDF flows) strips the Applire
namespace, the IPTC property **and** every Info-dictionary key; the output carries Ghostscript's own
metadata instead. The document's text is untouched. Many ATS platforms and job portals re-render or
normalise an uploaded PDF the same way.

So the honest statement of what this achieves is: **the document is marked at generation, and the mark
survives until the first party who re-processes it.** That is a property of the metadata layer, not a
defect in the implementation — the Guidelines' "robust and reliable" standard (¶79–80) is assessed
holistically against the state of the art, and for a document that exists to be uploaded there is no
layer that survives an adversary or a normaliser. Applire's test suite runs that round-trip on every CI
run and records the result, so if the behaviour ever changes, this page changes with it.

## Deliberately out of scope

- **Token-level text watermarking.** That is the model provider's layer (¶27), and under Applire's
  bring-your-own-model design the operator chooses the model. No major API provider currently ships a
  detector a third party can call, so relying on an upstream watermark would mean asserting something
  nobody can verify.
- **A visible "AI-generated" label** on your CV. Article 50(2) asks for machine-readable detectability;
  Article 50(4)'s visible-disclosure duty is about published public-interest text, not job applications.
- **A C2PA cryptographic manifest.** It requires a signing identity and key custody that a self-hosted
  instance would have to hold. The IPTC vocabulary above is chosen so that a future C2PA assertion would
  say the same thing.

## Verifying it yourself

```bash
# PDF — read the XMP packet and the Info dictionary back out
python3 - <<'EOF'
from applire.services.pdf_provenance import read_provenance, is_marked
pdf = open("your-cv.pdf", "rb").read()
print(is_marked(pdf))
print(read_provenance(pdf))
EOF

# DOCX — the custom document properties
python3 -c "
from applire.services.office_export.provenance import read_document_provenance
print(read_document_provenance(open('your-cv.docx','rb').read()))
"

# Or without Applire at all: exiftool your-cv.pdf | grep -i 'ai\|digital source'
#                             unzip -p your-cv.docx docProps/custom.xml
```
