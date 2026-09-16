---
name: make_pptx
description: Generate a simple Chinese-friendly PowerPoint (.pptx) deck from slide
  data (title + bullet points) using python-pptx, writing the file to a given path.
input_schema:
  type: object
  properties:
    output_path:
      type: string
      description: Path to write the .pptx file, e.g. reports/deck.pptx
    title:
      type: string
      description: Deck title, rendered on the first slide.
    slides:
      type: array
      description: Content slides, in order.
      items:
        type: object
        properties:
          title:
            type: string
          bullets:
            type: array
            items:
              type: string
        required:
        - title
        - bullets
    font:
      type: string
      description: "CJK typeface name to apply, default '\u5FAE\u8F6F\u96C5\u9ED1\
        ' (Microsoft YaHei)."
  required:
  - output_path
  - title
  - slides
---

# make_pptx

Generate a simple Chinese-friendly PowerPoint (.pptx) deck from slide data (title + bullet points) using python-pptx, writing the file to a given path.
