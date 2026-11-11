import type { TextMessagePartComponent } from "@assistant-ui/react";
import type { AnchorHTMLAttributes } from "react";
import Markdown, { type Components, type Options } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { Caret } from "@/components/atoms/caret";
import { LINK_REL, sanitizeSchema } from "@/utils/markdown";

type Plugins = NonNullable<Options["rehypePlugins"]>;

const remarkPlugins: Plugins = [remarkGfm];
const rehypePlugins: Plugins = [[rehypeSanitize, sanitizeSchema]];

function Link({ node, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { node?: unknown }) {
  // The hast node is for custom renderers; it must not reach the DOM.
  void node;
  return <a {...props} rel={LINK_REL} target="_blank" />;
}

const components: Components = { a: Link };

/**
 * An answer as sanitised markdown. Raw HTML is skipped before parsing and the
 * sanitiser drops anything the schema does not list, so an answer can never
 * run script or load a frame.
 */
export const MarkdownText: TextMessagePartComponent = ({ text, status }) => {
  const streaming = status.type === "running";
  return (
    <div
      className="prose-chat max-w-none text-md text-fg"
      aria-busy={streaming || undefined}
      data-testid="assistant-message"
    >
      <Markdown
        skipHtml
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {text}
      </Markdown>
      {streaming ? <Caret /> : null}
    </div>
  );
};
