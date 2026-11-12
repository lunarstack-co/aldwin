import { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";

/**
 * The sanitiser's default schema, plus `language-math` / `math-inline` /
 * `math-display` classes on `code` so `remark-math` spans survive sanitisation
 * before `rehype-katex` renders them. Raw HTML in markdown is dropped first.
 */
export const sanitizeSchema: SanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code ?? []),
      ["className", "language-math", "math-inline", "math-display"],
    ],
  },
};

export const LINK_REL = "noopener noreferrer";

export function allowsTag(tag: string): boolean {
  return (sanitizeSchema.tagNames ?? []).includes(tag);
}

export function allowsProtocol(attribute: string, protocol: string): boolean {
  return (sanitizeSchema.protocols?.[attribute] ?? []).includes(protocol);
}
