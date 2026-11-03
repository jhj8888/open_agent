import rehypeSanitize, { defaultSchema, type Options } from 'rehype-sanitize';

const activeContentTags = [
  'base',
  'embed',
  'form',
  'iframe',
  'link',
  'meta',
  'object',
  'script',
  'style',
];

/**
 * GitHub-compatible Markdown HTML with the minimum KaTeX input classes.
 * Sanitization runs before KaTeX so only trusted renderer output may add the
 * richer MathML/SVG markup that KaTeX needs.
 */
export const markdownSanitizeSchema: Options = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [['className', /^language-./, 'math-inline', 'math-display']],
  },
  strip: [...new Set([...(defaultSchema.strip ?? []), ...activeContentTags])],
};

export const markdownSanitizePlugin: [typeof rehypeSanitize, Options] = [
  rehypeSanitize,
  markdownSanitizeSchema,
];

/** Keep KaTeX's unsafe HTML and URL commands disabled for untrusted Markdown. */
export const markdownKatexOptions = {
  trust: false,
  strict: 'warn' as const,
};
