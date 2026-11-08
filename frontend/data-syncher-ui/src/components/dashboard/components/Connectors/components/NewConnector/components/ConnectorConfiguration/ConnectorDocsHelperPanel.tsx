import { useEffect, useMemo, useState } from "react";

import {
  Box,
  Button,
  Flex,
  Image,
  Link,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";

import { FiExternalLink, FiFileText, FiHome, FiLink } from "react-icons/fi";

import { toaster } from "@/components/ui/toaster";

const DEFAULT_SITE_ORIGIN = "https://qa-kubernetes.datasyncher.com";

type GuideImage =
  | string
  | {
      src?: string;
      url?: string;
      alt?: string;
      caption?: string;
    };

type GuideMediaItem = {
  type?: string;
  src?: string;
  url?: string;
  alt?: string;
  caption?: string;
};

type RelatedLink = {
  label: string;
  href: string;
};

const getOriginFromUrl = (value?: string) => {
  if (!value) return null;
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

const getAssetUrlCandidates = (src: string, assetsBaseUrl?: string) => {
  if (!src) return [];
  if (/^https?:\/\//i.test(src)) return [src];

  const candidates: string[] = [];
  const origin =
    getOriginFromUrl(assetsBaseUrl) || "https://qa-kubernetes.datasyncher.com";
  const base =
    assetsBaseUrl && /^https?:\/\//i.test(assetsBaseUrl)
      ? assetsBaseUrl.replace(/\/$/, "")
      : null;

  if (src.startsWith("/")) {
    // Try with assetsBaseUrl first (commonly ".../docs"), then root, then "/docs" prefix.
    if (base) candidates.push(`${base}${src}`);
    candidates.push(`${origin}${src}`);
    candidates.push(`${origin}/docs${src}`);
    return Array.from(new Set(candidates));
  }

  if (base) candidates.push(`${base}/${src.replace(/^\//, "")}`);
  candidates.push(`${origin}/${src.replace(/^\//, "")}`);
  candidates.push(`${origin}/docs/${src.replace(/^\//, "")}`);
  return Array.from(new Set(candidates));
};

const GuideImage = ({
  src,
  alt,
  caption,
  assetsBaseUrl,
}: {
  src: string;
  alt: string;
  caption?: string;
  assetsBaseUrl?: string;
}) => {
  const candidates = useMemo(
    () => getAssetUrlCandidates(src, assetsBaseUrl),
    [src, assetsBaseUrl],
  );
  const [candidateIndex, setCandidateIndex] = useState(0);
  const resolved =
    candidates[candidateIndex] || resolveAssetUrl(src, assetsBaseUrl);
  const exhausted =
    candidates.length > 0 && candidateIndex >= candidates.length - 1;

  return (
    <Box>
      <Image
        src={resolved}
        alt={alt}
        borderRadius="md"
        border="1px solid"
        borderColor="gray.200"
        w="full"
        loading="lazy"
        onError={() => {
          if (candidateIndex < candidates.length - 1) {
            setCandidateIndex((i) => i + 1);
          }
        }}
      />
      {caption && (
        <Text fontSize="xs" color="gray.600" mt={1}>
          {caption}
        </Text>
      )}
      {exhausted && (
        <Text fontSize="xs" color="gray.500" mt={1}>
          Image failed to load.{" "}
          <Link
            href={resolved}
            target="_blank"
            rel="noreferrer"
            color="brand.600"
            textDecoration="underline"
          >
            Open image
          </Link>
        </Text>
      )}
    </Box>
  );
};

type GuideBlock = {
  type?: string;
  text?: string;
  variant?: string;
  summary?: string;
  blocks?: unknown;
  items?: unknown;
  media?: unknown;
};

type GuideListItem = {
  text?: string;
  media?: unknown;
};

const GuideBlocks = ({
  blocks,
  assetsBaseUrl,
  stepTitle,
}: {
  blocks: unknown;
  assetsBaseUrl?: string;
  stepTitle: string;
}) => {
  if (!Array.isArray(blocks) || blocks.length === 0) return null;

  const renderMediaImages = (media: unknown, fallbackAlt: string) => {
    const images =
      Array.isArray(media) && media.length > 0
        ? (media.filter(isRecord) as Record<string, unknown>[])
            .map((m) => ({
              type: typeof m.type === "string" ? m.type : undefined,
              src: typeof m.src === "string" ? m.src : undefined,
              url: typeof m.url === "string" ? m.url : undefined,
              alt: typeof m.alt === "string" ? m.alt : undefined,
              caption: typeof m.caption === "string" ? m.caption : undefined,
            }))
            .filter((m) => m.type === "image" && (m.src || m.url))
        : [];

    if (images.length === 0) return null;

    return (
      <VStack gap={3} align="stretch" mt={3}>
        {images.map((img, idx) => (
          <GuideImage
            key={`${img.src || img.url || ""}-${idx}`}
            src={img.src || img.url || ""}
            alt={img.alt || fallbackAlt}
            caption={img.caption}
            assetsBaseUrl={assetsBaseUrl}
          />
        ))}
      </VStack>
    );
  };

  const renderList = (ordered: boolean, items: unknown) => {
    if (!Array.isArray(items) || items.length === 0) return null;
    const listItems = items.filter(isRecord) as Record<string, unknown>[];

    const asTag = ordered ? "ol" : "ul";
    const listStyleType = ordered ? "decimal" : "disc";

    return (
      <Box as={asTag} pl={5} m={0} style={{ listStyleType }}>
        {listItems.map((raw, idx) => {
          const item = raw as GuideListItem;
          const text = normalizeGuideText(
            typeof item.text === "string" ? item.text : "",
          );
          const fallbackAlt = stepTitle || "S3 setup step";

          return (
            <Box as="li" key={idx} mt={idx === 0 ? 0 : 2}>
              <Text fontSize="sm" color="gray.700" whiteSpace="pre-line">
                {renderInline(text)}
              </Text>
              {renderMediaImages(item.media, fallbackAlt)}
            </Box>
          );
        })}
      </Box>
    );
  };

  return (
    <VStack gap={3} align="stretch" mt={2}>
      {blocks.map((raw, idx) => {
        if (!isRecord(raw)) return null;
        const block = raw as GuideBlock;
        const type = typeof block.type === "string" ? block.type : "paragraph";

        if (type === "heading") {
          return (
            <Text key={idx} fontWeight="semibold" color="gray.900" mt={2}>
              {block.text}
            </Text>
          );
        }

        if (type === "paragraph") {
          const text = normalizeGuideText(block.text);
          if (!text) return null;
          return (
            <Text
              key={idx}
              fontSize="sm"
              color="gray.700"
              whiteSpace="pre-line"
            >
              {renderInline(text)}
            </Text>
          );
        }

        if (type === "callout") {
          const text = normalizeGuideText(block.text);
          if (!text) return null;
          return (
            <Box
              key={idx}
              border="1px solid"
              borderColor="gray.200"
              bg="gray.50"
              borderRadius="md"
              p={3}
            >
              <Text fontSize="sm" color="gray.800" whiteSpace="pre-line">
                {text}
              </Text>
            </Box>
          );
        }

        if (type === "unordered_list") {
          return <Box key={idx}>{renderList(false, block.items)}</Box>;
        }

        if (type === "ordered_list") {
          return <Box key={idx}>{renderList(true, block.items)}</Box>;
        }

        if (type === "details") {
          const summary =
            typeof block.summary === "string" ? block.summary : "Details";
          return (
            <Box
              key={idx}
              as="details"
              border="1px solid"
              borderColor="gray.200"
              borderRadius="md"
              bg="white"
              p={3}
            >
              <Box as="summary" cursor="pointer">
                <Text fontSize="sm" fontWeight="semibold" color="gray.900">
                  {summary}
                </Text>
              </Box>
              <Box mt={2}>
                <GuideBlocks
                  blocks={block.blocks}
                  assetsBaseUrl={assetsBaseUrl}
                  stepTitle={stepTitle}
                />
              </Box>
            </Box>
          );
        }

        // Fallback: try to render as a paragraph.
        const fallback = normalizeGuideText(block.text);
        if (!fallback) return null;
        return (
          <Text key={idx} fontSize="sm" color="gray.700" whiteSpace="pre-line">
            {renderInline(fallback)}
          </Text>
        );
      })}
    </VStack>
  );
};

type GuideStep = {
  id?: string;
  title?: string;
  heading?: string;
  description?: string;
  body?: string;
  content?: string;
  text?: string;
  fields?: string[];
  images?: GuideImage[];
  media?: GuideMediaItem[];
  blocks?: unknown;
};

type GuideDoc = {
  title?: string;
  docsUrl?: string;
  url?: string;
  assetsBaseUrl?: string;
  steps?: GuideStep[];
  relatedLinks?: RelatedLink[];
};

type ConnectorGuideKind = "connector" | "destination";

type ConnectorDocsHelperPanelProps = {
  connectorKey: string;
  kind?: ConnectorGuideKind;
};

const normalizeConnectorKey = (value: string) =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");

const uniqStrings = (values: string[]) =>
  Array.from(new Set(values.filter(Boolean)));

const GUIDE_URL_MAP: Record<
  ConnectorGuideKind,
  Record<string, { jsonUrl: string; docsUrl?: string }>
> = {
  connector: {
    // Use explicit mappings where possible to avoid probing multiple candidate
    // URLs (which results in extra requests that are often aborted/cancelled).
    awss3: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/aws-s3.json",
    },
    amazons3: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/aws-s3.json",
    },
    microsoftdynamics365fo: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/dynamics-365-fo.json",
    },
    snowflake: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/snowflake-source.json",
    },
    salesforce: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/salesforce.json",
    },
    salesforcesandbox: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/salesforce-sandbox.json",
    },
    googlereviews: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/connectors/google-reviews.json",
    },
    // File sources reuse destination guide JSON until dedicated source guides exist.
    sftp: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/sftp-destination.json",
    },
    googledrive: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/google-drive-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/google-drive-destination",
    },
    azuredatalakestorage: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/adls-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/adls-destination",
    },
    adls: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/adls-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/adls-destination",
    },
  },
  destination: {
    amazons3: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/amazon-s3-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/amazon-s3-destination",
    },
    snowflake: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/snowflake.json",
    },
    salesforce: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/salesforce-destination.json",
    },
    salesforcesandbox: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/salesforce-sandbox-destination.json",
    },
    sharepoint: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/sharepoint-destination.json",
    },
    sftp: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/sftp-destination.json",
    },
    googledrive: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/google-drive-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/google-drive-destination",
    },
    azuredatalakestorage: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/adls-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/adls-destination",
    },
    adls: {
      jsonUrl:
        "https://qa-kubernetes.datasyncher.com/docs/guides/destinations/adls-destination.json",
      docsUrl:
        "https://qa-kubernetes.datasyncher.com/docs/destinations/adls-destination",
    },
  },
};

const getMappedGuide = ({
  connectorKey,
  kind,
}: {
  connectorKey: string;
  kind: ConnectorGuideKind;
}) => {
  const normalized = normalizeConnectorKey(connectorKey);
  return GUIDE_URL_MAP[kind]?.[normalized] ?? null;
};

const getSlugCandidates = (connectorKey: string) => {
  const raw = connectorKey.trim();
  const normalized = normalizeConnectorKey(connectorKey);
  const candidates: string[] = [];

  // Special-case common connector slugs that differ from backend keys.
  if (normalized.includes("amazons3")) {
    candidates.push("aws-s3", "amazon-s3", "amazons3");
  }

  candidates.push(raw, normalized);

  // Some docs use dashed slugs; try a best-effort dashed variant.
  if (raw && !raw.includes("-")) {
    candidates.push(raw.replace(/([a-z])([A-Z])/g, "$1-$2").toLowerCase());
  }

  return uniqStrings(candidates);
};

const buildGuideCandidateUrls = ({
  connectorKey,
  kind,
}: {
  connectorKey: string;
  kind: ConnectorGuideKind;
}) => {
  const trimmed = connectorKey.trim();
  if (/^https?:\/\//i.test(trimmed)) return [trimmed];

  const mapped = getMappedGuide({ connectorKey, kind });
  if (mapped?.jsonUrl) return [mapped.jsonUrl];

  const origin = DEFAULT_SITE_ORIGIN.replace(/\/$/, "");
  const slugs = getSlugCandidates(connectorKey);
  const collections =
    kind === "destination" ? ["destinations", "connectors"] : ["connectors"];

  const urls: string[] = [];
  const MAX_CANDIDATE_URLS = 18;

  for (const collection of collections) {
    for (const slug of slugs) {
      const baseUrls = [
        // Preferred current location
        `${origin}/docs/guides/${collection}/${slug}.json`,
        // Common fallbacks
        `${origin}/docs/${collection}/${slug}.json`,
        `${origin}/guides/${collection}/${slug}.json`,
        // Legacy (connectors sometimes exposed here)
        `${origin}/docs/connectors/${slug}.json`,
      ];

      for (const baseUrl of baseUrls) {
        urls.push(baseUrl);
        // Some CDNs/proxies only return JSON with raw=1.
        urls.push(`${baseUrl}?raw=1`);
        if (urls.length >= MAX_CANDIDATE_URLS) break;
      }
      if (urls.length >= MAX_CANDIDATE_URLS) break;
    }
    if (urls.length >= MAX_CANDIDATE_URLS) break;
  }

  return uniqStrings(urls);
};

const buildDocsPageUrl = ({
  connectorKey,
  kind,
}: {
  connectorKey: string;
  kind: ConnectorGuideKind;
}) => {
  const mapped = getMappedGuide({ connectorKey, kind });
  if (mapped?.docsUrl) return mapped.docsUrl;

  const slug = getSlugCandidates(connectorKey)[0] || "";
  if (!slug) return "";
  const collection = kind === "destination" ? "destinations" : "connectors";
  return `${DEFAULT_SITE_ORIGIN}/docs/${collection}/${slug}`;
};

const guideCache = new Map<string, GuideDoc | null>();
const guidePromiseCache = new Map<string, Promise<GuideDoc | null>>();

const resolveAssetUrl = (src: string, assetsBaseUrl?: string) => {
  if (/^https?:\/\//i.test(src)) return src;
  if (src.startsWith("/")) {
    if (assetsBaseUrl && /^https?:\/\//i.test(assetsBaseUrl)) {
      return `${assetsBaseUrl.replace(/\/$/, "")}${src}`;
    }
    return `${DEFAULT_SITE_ORIGIN}${src}`;
  }
  if (!assetsBaseUrl) return src;

  return `${assetsBaseUrl.replace(/\/$/, "")}/${src.replace(/^\//, "")}`;
};

const looksLikeHtml = (text: string) => {
  const trimmed = text.trimStart().toLowerCase();
  return trimmed.startsWith("<!doctype html") || trimmed.startsWith("<html");
};

const safeJsonParse = (text: string) => {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
};

const normalizeGuide = (raw: unknown): GuideDoc | null => {
  if (!raw || typeof raw !== "object") return null;

  const doc = raw as Record<string, unknown>;
  const stepsRaw = doc.steps;
  const steps = Array.isArray(stepsRaw) ? (stepsRaw as unknown[]) : [];

  const normalizedSteps: GuideStep[] = steps
    .map((step) => {
      if (!step || typeof step !== "object") return null;
      const s = step as Record<string, unknown>;
      const title =
        (typeof s.title === "string" && s.title) ||
        (typeof s.heading === "string" && s.heading) ||
        undefined;
      const body =
        (typeof s.description === "string" && s.description) ||
        (typeof s.body === "string" && s.body) ||
        (typeof s.content === "string" && s.content) ||
        (typeof s.text === "string" && s.text) ||
        undefined;
      const fields = Array.isArray(s.fields)
        ? (s.fields.filter((f) => typeof f === "string") as string[])
        : undefined;
      const blocks = s.blocks;

      // Support both `images` and `media` (as in the JSON you pasted).
      const images = Array.isArray(s.images)
        ? (s.images as GuideImage[])
        : undefined;
      const media = Array.isArray(s.media)
        ? (s.media
            .filter((m) => m && typeof m === "object")
            .map((m) => m as GuideMediaItem) as GuideMediaItem[])
        : undefined;

      return {
        id: typeof s.id === "string" ? s.id : undefined,
        title,
        description: body,
        fields,
        images,
        media,
        blocks,
      } satisfies GuideStep;
    })
    .filter(Boolean) as GuideStep[];

  const title = typeof doc.title === "string" ? doc.title : undefined;
  const docsUrl =
    typeof doc.docsUrl === "string"
      ? doc.docsUrl
      : typeof doc.docs_url === "string"
        ? (doc.docs_url as string)
        : undefined;
  const assetsBaseUrl =
    typeof doc.assetsBaseUrl === "string"
      ? doc.assetsBaseUrl
      : typeof doc.assets_base_url === "string"
        ? (doc.assets_base_url as string)
        : undefined;

  const relatedLinksRaw =
    (Array.isArray(doc.relatedLinks) && doc.relatedLinks) ||
    (Array.isArray(doc.related_links) && doc.related_links) ||
    [];
  const relatedLinks = (relatedLinksRaw as unknown[])
    .filter((l) => l && typeof l === "object")
    .map((l) => l as Record<string, unknown>)
    .map((l) => ({
      label: typeof l.label === "string" ? l.label : "",
      href: typeof l.href === "string" ? l.href : "",
    }))
    .filter((l) => l.label && l.href);

  return {
    title,
    docsUrl,
    assetsBaseUrl,
    steps: normalizedSteps,
    relatedLinks,
  };
};

const fetchGuideOnce = async ({
  cacheKey,
  candidateUrls,
}: {
  cacheKey: string;
  candidateUrls: string[];
}): Promise<GuideDoc | null> => {
  if (guideCache.has(cacheKey)) return guideCache.get(cacheKey) ?? null;
  const existingPromise = guidePromiseCache.get(cacheKey);
  if (existingPromise) return existingPromise;

  const promise = (async () => {
    const TIMEOUT_MS = 2500;
    const CONCURRENCY = 4;

    const urls = candidateUrls.slice(0, 24);
    let index = 0;
    let found: GuideDoc | null = null;
    const controllers: AbortController[] = [];

    const fetchFromUrl = async (url: string) => {
      const controller = new AbortController();
      controllers.push(controller);

      const timeoutId = window.setTimeout(() => controller.abort(), TIMEOUT_MS);

      try {
        const response = await fetch(url, {
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) return null;

        const text = await response.text();
        if (looksLikeHtml(text)) return null;

        const parsed = safeJsonParse(text);
        if (!parsed) return null;

        const normalized = normalizeGuide(parsed);
        if (normalized && (normalized.steps?.length ?? 0) > 0) {
          return normalized;
        }

        return null;
      } catch {
        return null;
      } finally {
        window.clearTimeout(timeoutId);
      }
    };

    const worker = async () => {
      while (!found) {
        const currentIndex = index;
        index += 1;
        if (currentIndex >= urls.length) return;

        const url = urls[currentIndex];
        const result = await fetchFromUrl(url);
        if (!result) continue;

        found = result;
        controllers.forEach((c) => c.abort());
        return;
      }
    };

    await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));

    if (found) {
      guideCache.set(cacheKey, found);
      return found;
    }

    guideCache.set(cacheKey, null);
    return null;
  })();

  guidePromiseCache.set(cacheKey, promise);
  return promise;
};

const isPrerequisitesStep = (step: GuideStep) => {
  const title = (step.title || step.heading || "").toLowerCase();
  return title.includes("prerequisite");
};

const normalizeGuideText = (value?: string) =>
  (value ?? "").replace(/\r\n/g, "\n").trim();

const splitBlocks = (value: string) =>
  value
    .split(/\n{2,}/g)
    .map((b) => b.trim())
    .filter(Boolean);

type InlineToken =
  | { type: "text"; value: string }
  | { type: "link"; label: string; href: string }
  | { type: "bold"; value: string }
  | { type: "code"; value: string };

const tokenizeInline = (text: string): InlineToken[] => {
  const tokens: InlineToken[] = [];
  let cursor = 0;

  const nextMatch = () => {
    const rest = text.slice(cursor);
    const link = rest.match(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/);
    const bold = rest.match(/\*\*([^*]+)\*\*/);
    const code = rest.match(/`([^`]+)`/);

    const matches = [
      link
        ? { kind: "link" as const, index: link.index ?? 0, match: link }
        : null,
      bold
        ? { kind: "bold" as const, index: bold.index ?? 0, match: bold }
        : null,
      code
        ? { kind: "code" as const, index: code.index ?? 0, match: code }
        : null,
    ].filter(Boolean) as Array<{
      kind: "link" | "bold" | "code";
      index: number;
      match: RegExpMatchArray;
    }>;

    if (matches.length === 0) return null;
    matches.sort((a, b) => a.index - b.index);
    return matches[0];
  };

  while (cursor < text.length) {
    const found = nextMatch();
    if (!found) {
      tokens.push({ type: "text", value: text.slice(cursor) });
      break;
    }

    const absoluteIndex = cursor + found.index;
    if (absoluteIndex > cursor) {
      tokens.push({ type: "text", value: text.slice(cursor, absoluteIndex) });
    }

    const full = found.match[0] ?? "";
    if (found.kind === "link") {
      const label = found.match[1] ?? "";
      const href = found.match[2] ?? "";
      tokens.push({ type: "link", label, href });
    } else if (found.kind === "bold") {
      const value = found.match[1] ?? "";
      tokens.push({ type: "bold", value });
    } else {
      const value = found.match[1] ?? "";
      tokens.push({ type: "code", value });
    }

    cursor = absoluteIndex + full.length;
  }

  return tokens;
};

const renderInline = (text: string) => {
  const tokens = tokenizeInline(text);
  return tokens.map((t, idx) => {
    if (t.type === "text") return <span key={idx}>{t.value}</span>;
    if (t.type === "bold") return <strong key={idx}>{t.value}</strong>;
    if (t.type === "code") {
      return (
        <Box
          key={idx}
          as="code"
          px={1}
          py={0.5}
          borderRadius="sm"
          bg="gray.100"
          fontSize="0.85em"
          fontFamily="mono"
        >
          {t.value}
        </Box>
      );
    }
    return (
      <Link
        key={idx}
        href={t.href}
        target="_blank"
        rel="noreferrer"
        color="brand.600"
        textDecoration="underline"
      >
        {t.label}
      </Link>
    );
  });
};

const GuideRichText = ({ text }: { text?: string }) => {
  const normalized = normalizeGuideText(text);
  if (!normalized) return null;

  const blocks = splitBlocks(normalized);

  return (
    <VStack gap={3} align="stretch" mt={2}>
      {blocks.map((block, blockIndex) => {
        const lines = block
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean);

        const isCallout =
          /^>/.test(lines[0] ?? "") ||
          /^(note|tip|important)\s*:/i.test(lines[0] ?? "");

        if (isCallout) {
          const calloutText = lines
            .map((l) => l.replace(/^>\s?/, ""))
            .join("\n");
          return (
            <Box
              key={blockIndex}
              border="1px solid"
              borderColor="gray.200"
              bg="gray.50"
              borderRadius="md"
              p={3}
            >
              <Text fontSize="sm" color="gray.800" whiteSpace="pre-line">
                {calloutText}
              </Text>
            </Box>
          );
        }

        const isUnorderedList = lines.every((l) => /^(-|\*|\u2022)\s+/.test(l));
        if (isUnorderedList) {
          return (
            <Box
              key={blockIndex}
              as="ul"
              pl={5}
              m={0}
              style={{ listStyleType: "disc" }}
            >
              {lines.map((l, liIndex) => {
                const itemText = l.replace(/^(-|\*|\u2022)\s+/, "");
                return (
                  <Text
                    key={liIndex}
                    as="li"
                    fontSize="sm"
                    color="gray.700"
                    mt={liIndex === 0 ? 0 : 2}
                  >
                    {renderInline(itemText)}
                  </Text>
                );
              })}
            </Box>
          );
        }

        const isOrderedList = lines.every((l) => /^\d+[.)]\s+/.test(l));
        if (isOrderedList) {
          return (
            <Box
              key={blockIndex}
              as="ol"
              pl={5}
              m={0}
              style={{ listStyleType: "decimal" }}
            >
              {lines.map((l, liIndex) => {
                const itemText = l.replace(/^\d+[.)]\s+/, "");
                return (
                  <Text
                    key={liIndex}
                    as="li"
                    fontSize="sm"
                    color="gray.700"
                    mt={liIndex === 0 ? 0 : 2}
                  >
                    {renderInline(itemText)}
                  </Text>
                );
              })}
            </Box>
          );
        }

        return (
          <Text
            key={blockIndex}
            fontSize="sm"
            color="gray.700"
            whiteSpace="pre-line"
          >
            {renderInline(block)}
          </Text>
        );
      })}
    </VStack>
  );
};

const ConnectorDocsHelperPanel = ({
  connectorKey,
  kind = "connector",
}: ConnectorDocsHelperPanelProps) => {
  const [guide, setGuide] = useState<GuideDoc | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);

  const normalizedConnectorKey = connectorKey
    ? normalizeConnectorKey(connectorKey)
    : "";
  const requestKey = connectorKey
    ? `${kind}:${normalizedConnectorKey || connectorKey}`
    : null;
  const isLoading = !!requestKey && loadedKey !== requestKey;

  useEffect(() => {
    let isMounted = true;

    if (!requestKey) {
      return () => {
        isMounted = false;
      };
    }

    const candidateUrls = buildGuideCandidateUrls({ connectorKey, kind });

    fetchGuideOnce({ cacheKey: requestKey, candidateUrls })
      .then((data) => {
        if (isMounted) setGuide(data);
      })
      .finally(() => {
        if (isMounted) setLoadedKey(requestKey);
      });

    return () => {
      isMounted = false;
    };
  }, [connectorKey, kind, requestKey]);

  const effectiveGuide = requestKey ? guide : null;
  const steps = useMemo(
    () => effectiveGuide?.steps ?? [],
    [effectiveGuide?.steps],
  );
  const docsUrl =
    effectiveGuide?.docsUrl ||
    effectiveGuide?.url ||
    buildDocsPageUrl({ connectorKey, kind });
  const relatedLinks = useMemo(
    () => effectiveGuide?.relatedLinks ?? [],
    [effectiveGuide?.relatedLinks],
  );

  const { prerequisiteSteps, instructionSteps } = useMemo(() => {
    const prerequisiteSteps = steps.filter(isPrerequisitesStep);
    const instructionSteps = steps.filter((s) => !isPrerequisitesStep(s));
    return { prerequisiteSteps, instructionSteps };
  }, [steps]);

  const copyToClipboard = async (value: string) => {
    if (!value) return false;

    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // fall through to legacy fallback
    }

    try {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "true");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(textarea);
      return ok;
    } catch {
      return false;
    }
  };

  const handleCopyDocsLink = async () => {
    if (typeof window === "undefined") return;
    const ok = await copyToClipboard(docsUrl);
    if (ok) {
      toaster.success({
        title: "Link copied",
        description: "Docs link copied to clipboard.",
      });
      return;
    }

    toaster.error({
      title: "Copy failed",
      description: "Could not copy link. Please copy it from the address bar.",
    });
  };

  const header = (
    <Flex
      align="center"
      justify="space-between"
      gap={3}
      px={4}
      py={3}
      bg="gray.100"
      borderBottom="1px solid"
      borderColor="gray.200"
    >
      <Flex align="center" gap={2} minW={0}>
        <Text fontWeight="semibold" color="gray.900" lineClamp={1}>
          Setup Guide
        </Text>
        <Button
          aria-label="Copy docs link"
          title="Copy docs link"
          size="xs"
          variant="ghost"
          onClick={handleCopyDocsLink}
          minW="28px"
          h="28px"
          px={0}
        >
          <FiLink />
        </Button>
      </Flex>

      <Link
        href={docsUrl}
        target="_blank"
        rel="noreferrer"
        color="brand.600"
        display="inline-flex"
        alignItems="center"
        gap={1}
        fontSize="sm"
        flexShrink={0}
      >
        View on docs site
        <FiExternalLink />
      </Link>
    </Flex>
  );

  return (
    <Flex direction="column" w="full" h="full" minH={0}>
      {header}
      <Box px={4} py={4} bg="gray.50" overflowY="auto" flex="1" minH={0}>
        {isLoading ? (
          <Flex justify="center" py={8}>
            <Spinner size="sm" color="brand.500" />
          </Flex>
        ) : steps.length > 0 ? (
          <VStack gap={6} align="stretch">
            {prerequisiteSteps.length > 0 && (
              <Box>
                <Text fontSize="md" fontWeight="semibold" color="gray.900">
                  Prerequisites
                </Text>
                <VStack gap={3} align="stretch" mt={3}>
                  {prerequisiteSteps.map((step, index) => {
                    const title = step.title || step.heading || "";
                    const body =
                      step.description ||
                      step.body ||
                      step.content ||
                      step.text ||
                      "";
                    const stepTitle = title || "Prerequisites";

                    return (
                      <Box
                        key={step.id || `${step.title}-${index}`}
                        border="1px solid"
                        borderColor="gray.200"
                        borderRadius="lg"
                        bg="white"
                        boxShadow="sm"
                        p={4}
                      >
                        {title && title.toLowerCase() !== "prerequisites" && (
                          <Text fontWeight="semibold" color="gray.900">
                            {title}
                          </Text>
                        )}
                        {Array.isArray(step.blocks) ? (
                          <GuideBlocks
                            blocks={step.blocks}
                            assetsBaseUrl={guide?.assetsBaseUrl}
                            stepTitle={stepTitle}
                          />
                        ) : (
                          <GuideRichText text={body} />
                        )}
                      </Box>
                    );
                  })}
                </VStack>
              </Box>
            )}

            {instructionSteps.length > 0 && (
              <Box>
                <Text fontSize="md" fontWeight="semibold" color="gray.900">
                  Setup instructions
                </Text>
                <VStack gap={4} align="stretch" mt={3}>
                  {instructionSteps.map((step, index) => {
                    const images = step.images ?? [];
                    const mediaImages =
                      step.media?.filter((m) => m.type === "image") ?? [];
                    const title =
                      step.title || step.heading || `Step ${index + 1}`;
                    const body =
                      step.description ||
                      step.body ||
                      step.content ||
                      step.text ||
                      "";
                    const stepNumber = index + 1;
                    const stepTitle = title || `Step ${stepNumber}`;
                    const hasBlocks = Array.isArray(step.blocks);
                    const allMediaImages = [...mediaImages];

                    return (
                      <Box
                        key={step.id || `${title}-${index}`}
                        border="1px solid"
                        borderColor="gray.200"
                        borderRadius="lg"
                        bg="white"
                        boxShadow="sm"
                        p={4}
                      >
                        <Flex align="center" gap={3} minW={0}>
                          <Box
                            px={3}
                            py={1}
                            borderRadius="full"
                            bg="brand.600"
                            color="white"
                            fontSize="xs"
                            fontWeight="bold"
                            letterSpacing="0.06em"
                            flexShrink={0}
                          >
                            {`STEP ${stepNumber}`}
                          </Box>
                          <Text
                            fontWeight="semibold"
                            color="gray.900"
                            fontSize="md"
                            lineClamp={2}
                          >
                            {title}
                          </Text>
                        </Flex>

                        {hasBlocks ? (
                          <GuideBlocks
                            blocks={step.blocks}
                            assetsBaseUrl={guide?.assetsBaseUrl}
                            stepTitle={stepTitle}
                          />
                        ) : (
                          <GuideRichText text={body} />
                        )}

                        {(images.length > 0 || allMediaImages.length > 0) && (
                          <VStack gap={3} align="stretch" mt={4}>
                            {images.map((image, imageIndex) => {
                              const src =
                                typeof image === "string"
                                  ? image
                                  : image.src || image.url || "";
                              const alt =
                                typeof image === "string"
                                  ? title || "S3 setup step"
                                  : image.alt || title || "S3 setup step";
                              const caption =
                                typeof image === "string"
                                  ? undefined
                                  : image.caption;

                              if (!src) return null;

                              return (
                                <GuideImage
                                  key={`${src}-${imageIndex}`}
                                  src={src}
                                  alt={alt}
                                  caption={caption}
                                  assetsBaseUrl={guide?.assetsBaseUrl}
                                />
                              );
                            })}
                            {allMediaImages.map((image, imageIndex) => {
                              const src = image.src || image.url || "";
                              if (!src) return null;
                              return (
                                <GuideImage
                                  key={`${src}-${imageIndex}`}
                                  src={src}
                                  alt={image.alt || title || "S3 setup step"}
                                  caption={image.caption}
                                  assetsBaseUrl={guide?.assetsBaseUrl}
                                />
                              );
                            })}
                          </VStack>
                        )}
                      </Box>
                    );
                  })}
                </VStack>
              </Box>
            )}

            {relatedLinks.length > 0 && (
              <Box pt={2}>
                <Text fontSize="lg" fontWeight="semibold" color="gray.900">
                  Related articles
                </Text>
                <VStack align="stretch" gap={3} mt={3}>
                  {relatedLinks.map((item) => {
                    const icon =
                      item.label.toLowerCase().includes("home") ||
                      item.href.toLowerCase().includes("/intro")
                        ? FiHome
                        : FiFileText;
                    return (
                      <Link
                        key={`${item.href}-${item.label}`}
                        href={item.href}
                        target="_blank"
                        rel="noreferrer"
                        color="brand.600"
                        display="inline-flex"
                        alignItems="center"
                        gap={2}
                        fontSize="sm"
                      >
                        <Box as={icon} color="brand.600" />
                        {item.label}
                      </Link>
                    );
                  })}
                </VStack>
              </Box>
            )}
          </VStack>
        ) : (
          <Text fontSize="sm" color="gray.600">
            Setup guide is not available right now.
          </Text>
        )}
      </Box>
    </Flex>
  );
};

export default ConnectorDocsHelperPanel;
