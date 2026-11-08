import { useEffect, useId, useRef, useState } from "react";

import {
  Badge,
  Button,
  CloseButton,
  Dialog,
  Flex,
  Portal,
  RadioGroup,
  Text,
} from "@chakra-ui/react";

import { FiAlertCircle, FiAlertTriangle, FiInfo } from "react-icons/fi";

export type ConfirmIcon = "warning" | "danger" | "info";
export type ConfirmTone = "default" | "danger";

export type ConfirmChoice = {
  id: string;
  label: string;
};

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description?: string;
  details?: string[];
  icon?: ConfirmIcon;
  confirmLabel: string;
  cancelLabel: string;
  tone?: ConfirmTone;
  alternate?: { label: string; tone?: ConfirmTone };
  choices?: ConfirmChoice[];
  blockBackdrop?: boolean;
  finalFocusEl?: () => HTMLElement | null;
  onConfirm: (_choiceId?: string) => void;
  onAlternate?: () => void;
  onCancel: () => void;
};

const ICONS = {
  warning: FiAlertTriangle,
  danger: FiAlertCircle,
  info: FiInfo,
} as const;

const ICON_COLOR = {
  warning: "orange.500",
  danger: "red.600",
  info: "brand.500",
} as const;

const dangerButtonProps = {
  bg: "red.700",
  color: "white",
  _hover: { bg: "red.800" },
} as const;

const ConfirmDialog = ({
  open,
  title,
  description,
  details,
  icon = "warning",
  confirmLabel,
  cancelLabel,
  tone = "default",
  alternate,
  choices,
  blockBackdrop = false,
  finalFocusEl,
  onConfirm,
  onAlternate,
  onCancel,
}: ConfirmDialogProps) => {
  const titleId = useId();
  const descriptionId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [selectedChoice, setSelectedChoice] = useState("");
  const Icon = ICONS[icon];
  const hasChoices = Boolean(choices?.length);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      cancelRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, title]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  return (
    <Dialog.Root
      lazyMount
      open={open}
      role="alertdialog"
      trapFocus
      restoreFocus
      closeOnEscape
      closeOnInteractOutside={!blockBackdrop}
      initialFocusEl={() => cancelRef.current}
      finalFocusEl={finalFocusEl}
      onOpenChange={(details) => {
        if (!details.open) onCancel();
      }}
      onInteractOutside={(event) => {
        if (blockBackdrop) event.preventDefault();
      }}
    >
      <Portal>
        <Dialog.Backdrop
          bg="blackAlpha.800"
          zIndex={1700}
          data-testid="confirm-backdrop"
          onClick={() => {
            if (!blockBackdrop) onCancel();
          }}
        />
        <Dialog.Positioner
          zIndex={1701}
          p={{ base: 0, md: 4 }}
          alignItems={{ base: "flex-end", md: "center" }}
        >
          <Dialog.Content
            bg="white"
            color="gray.800"
            borderRadius={{ base: "none", md: "xl" }}
            w={{ base: "100%", md: "480px" }}
            maxW="100%"
            m={0}
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
          >
            <Dialog.Header alignItems="flex-start" gap={3}>
              <Flex
                align="center"
                justify="center"
                boxSize="36px"
                borderRadius="full"
                bg={icon === "danger" ? "red.50" : "purple.50"}
                color={ICON_COLOR[icon]}
                flexShrink={0}
              >
                <Icon size={18} aria-hidden />
              </Flex>
              <Dialog.Title
                id={titleId}
                color="brand.800"
                fontSize="lg"
                lineHeight="short"
              >
                {title}
              </Dialog.Title>
            </Dialog.Header>
            <Dialog.CloseTrigger asChild>
              <CloseButton aria-label="Close" boxSize="44px" />
            </Dialog.CloseTrigger>
            <Dialog.Body>
              {description && (
                <Text id={descriptionId} fontSize="sm" color="gray.700">
                  {description}
                </Text>
              )}
              {details && details.length > 0 && (
                <Flex gap={2} wrap="wrap" mt={description ? 3 : 0}>
                  {details.map((detail) => (
                    <Badge
                      key={detail}
                      size="sm"
                      variant="outline"
                      colorPalette="brand"
                      borderRadius="full"
                      bg="white"
                    >
                      {detail}
                    </Badge>
                  ))}
                </Flex>
              )}
              {hasChoices && (
                <RadioGroup.Root
                  mt={4}
                  value={selectedChoice}
                  onValueChange={({ value }) => setSelectedChoice(value || "")}
                >
                  <Flex direction="column" gap={2}>
                    {choices?.map((choice) => (
                      <RadioGroup.Item
                        key={choice.id}
                        value={choice.id}
                        minH="44px"
                      >
                        <RadioGroup.ItemHiddenInput />
                        <RadioGroup.ItemIndicator />
                        <RadioGroup.ItemText>
                          {choice.label}
                        </RadioGroup.ItemText>
                      </RadioGroup.Item>
                    ))}
                  </Flex>
                </RadioGroup.Root>
              )}
            </Dialog.Body>
            <Dialog.Footer
              gap={2}
              flexDirection={{ base: "column", md: "row" }}
              alignItems="stretch"
            >
              <Button
                ref={cancelRef}
                variant="outline"
                minH="44px"
                onClick={onCancel}
                onKeyDown={(event) => {
                  if (event.key === "Enter") onCancel();
                }}
              >
                {cancelLabel}
              </Button>
              {alternate && (
                <Button
                  minH="44px"
                  variant={alternate.tone === "danger" ? "solid" : "outline"}
                  colorPalette={
                    alternate.tone === "danger" ? undefined : "brand"
                  }
                  {...(alternate.tone === "danger" ? dangerButtonProps : {})}
                  onClick={onAlternate}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") onAlternate?.();
                  }}
                >
                  {alternate.label}
                </Button>
              )}
              <Button
                minH="44px"
                colorPalette={tone === "danger" ? undefined : "brand"}
                {...(tone === "danger" ? dangerButtonProps : {})}
                disabled={hasChoices && !selectedChoice}
                onClick={() =>
                  onConfirm(hasChoices ? selectedChoice : undefined)
                }
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !(hasChoices && !selectedChoice)
                  ) {
                    onConfirm(hasChoices ? selectedChoice : undefined);
                  }
                }}
              >
                {confirmLabel}
              </Button>
            </Dialog.Footer>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default ConfirmDialog;
