import { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";

/** Default schema plus the math classes `remark-math` needs to survive sanitising before `rehype-katex` runs. */
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
