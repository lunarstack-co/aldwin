import { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";

/**
 * The sanitiser's default schema: no script, style, iframe or event handlers,
 * and only http, https, mailto and a few chat protocols in links. Raw HTML in
 * the markdown is dropped before it gets here.
 */
export const sanitizeSchema: SanitizeSchema = defaultSchema;

export const LINK_REL = "noopener noreferrer";

export function allowsTag(tag: string): boolean {
  return (sanitizeSchema.tagNames ?? []).includes(tag);
}

export function allowsProtocol(attribute: string, protocol: string): boolean {
  return (sanitizeSchema.protocols?.[attribute] ?? []).includes(protocol);
}
