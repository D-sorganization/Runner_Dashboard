/** Owner-authored inline Markdown (feature notes, decisions): untrusted, never raw HTML. */
import React from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

export function OwnerMarkdown({
  text,
  as: Tag = "div",
}: {
  text: string;
  /** Wrapper element; `span` keeps the text inline inside a list item. */
  as?: "div" | "span";
}): React.ReactElement {
  return (
    <Tag
      dangerouslySetInnerHTML={{
        __html: DOMPurify.sanitize(marked.parseInline(text, { async: false }), {
          ALLOWED_TAGS: ["a", "strong", "em", "code", "br"],
          ALLOWED_ATTR: ["href", "title"],
          ALLOWED_URI_REGEXP: /^https?:\/\//i,
        }),
      }}
    />
  );
}
