"""Transparency label generation (Milestone 5).

Maps a combined confidence (P(AI) in [0,1]) to one of three plain-language label
variants. The exact text here is the canonical copy mirrored in planning.md and README.
Labels are non-technical and non-accusatory, always state that the verdict is an
automated estimate, and (for the AI verdict) point to the appeal path.
"""

from detection import AI_THRESHOLD, HUMAN_THRESHOLD


def generate_label(confidence: float) -> dict:
    """Return the transparency label for a given confidence = P(AI).

    Returns {variant, title, body, display_text}. `display_text` is the full label as a
    reader would see it (title + body).
    """
    p_ai = round(confidence * 100)
    p_human = round((1 - confidence) * 100)

    if confidence >= AI_THRESHOLD:
        variant = "high_confidence_ai"
        title = "🤖 Likely AI-generated"
        body = (
            f"Our automated analysis suggests this content was most likely created with the "
            f"help of generative AI (estimated {p_ai}% likelihood). This is an automated "
            f"estimate, not a certainty. If you're the creator and believe this is wrong, "
            f"you can appeal this result."
        )
    elif confidence <= HUMAN_THRESHOLD:
        variant = "high_confidence_human"
        title = "✍️ Likely human-written"
        body = (
            f"Our automated analysis found no strong signs of AI generation in this content "
            f"(estimated {p_human}% likelihood it's human-written). This is an automated "
            f"estimate and not a guarantee of authorship."
        )
    else:
        variant = "uncertain"
        title = "❓ Not enough signal to tell"
        body = (
            "Our automated analysis couldn't confidently determine whether a person wrote "
            "this or it was generated with AI. Rather than risk mislabeling the creator's "
            "work, we're showing this note instead of a verdict."
        )

    return {
        "variant": variant,
        "title": title,
        "body": body,
        "display_text": f"{title}\n{body}",
    }


if __name__ == "__main__":
    # Milestone 5 verification: all three variants reachable & text matches spec.
    for c in (0.85, 0.55, 0.15):
        lab = generate_label(c)
        print(f"confidence={c}  variant={lab['variant']}")
        print(lab["display_text"])
        print("-" * 70)
