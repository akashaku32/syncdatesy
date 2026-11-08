/* eslint-disable react-refresh/only-export-components */
import { useSyncExternalStore } from "react";

import ConfirmDialog, {
  type ConfirmChoice,
  type ConfirmIcon,
  type ConfirmTone,
} from "./ConfirmDialog";

type ConfirmCommon = {
  title: string;
  description?: string;
  details?: string[];
  icon?: ConfirmIcon;
  confirmLabel: string;
  cancelLabel: string;
  tone?: ConfirmTone;
};

export type BinaryConfirmOptions = ConfirmCommon & {
  alternate?: undefined;
  choices?: undefined;
};

export type AlternateConfirmOptions = ConfirmCommon & {
  alternate: { label: string; tone?: ConfirmTone };
  choices?: undefined;
};

export type ChoiceConfirmOptions = ConfirmCommon & {
  choices: ConfirmChoice[];
  alternate?: undefined;
};

export type ConfirmOptions =
  | BinaryConfirmOptions
  | AlternateConfirmOptions
  | ChoiceConfirmOptions;

type ConfirmResult = boolean | "confirm" | "alternate" | "cancel" | string;

type PendingConfirm = {
  id: number;
  options: ConfirmOptions;
  trigger: HTMLElement | null;
  settled: boolean;
  resolve: (_value: ConfirmResult) => void;
};

let sequence = 0;
let current: PendingConfirm | null = null;
const queue: PendingConfirm[] = [];
const listeners = new Set<() => void>();

const emit = () => {
  listeners.forEach((listener) => listener());
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

const getSnapshot = () => current;

const cancelResult = (pending: PendingConfirm): ConfirmResult => {
  if (pending.options.alternate || pending.options.choices) return "cancel";
  return false;
};

const finish = (pending: PendingConfirm, value: ConfirmResult) => {
  if (!current || current.id !== pending.id || pending.settled) return;
  pending.settled = true;
  pending.resolve(value);
  const trigger = pending.trigger;
  current = queue.shift() ?? null;
  emit();
  queueMicrotask(() => trigger?.focus?.());
};

export function confirm(_options: ChoiceConfirmOptions): Promise<string>;
export function confirm(
  _options: AlternateConfirmOptions,
): Promise<"confirm" | "alternate" | "cancel">;
export function confirm(_options: BinaryConfirmOptions): Promise<boolean>;
export function confirm(options: ConfirmOptions): Promise<ConfirmResult> {
  return new Promise((resolve) => {
    const pending: PendingConfirm = {
      id: ++sequence,
      options,
      trigger:
        typeof document !== "undefined"
          ? (document.activeElement as HTMLElement | null)
          : null,
      settled: false,
      resolve,
    };
    if (current) {
      queue.push(pending);
      return;
    }
    current = pending;
    emit();
  });
}

export function useConfirmPending(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => current !== null,
    () => false,
  );
}

export const ConfirmHost = () => {
  const pending = useSyncExternalStore(subscribe, getSnapshot, () => null);
  if (!pending) return null;
  const { options } = pending;
  const blockBackdrop =
    options.tone === "danger" ||
    options.alternate?.tone === "danger" ||
    Boolean(options.choices?.length);

  return (
    <ConfirmDialog
      key={pending.id}
      open
      title={options.title}
      description={options.description}
      details={options.details}
      icon={options.icon}
      confirmLabel={options.confirmLabel}
      cancelLabel={options.cancelLabel}
      tone={options.tone}
      alternate={options.alternate}
      choices={options.choices}
      blockBackdrop={blockBackdrop}
      finalFocusEl={() => pending.trigger}
      onCancel={() => finish(pending, cancelResult(pending))}
      onAlternate={
        options.alternate ? () => finish(pending, "alternate") : undefined
      }
      onConfirm={(choiceId) => {
        if (options.choices) {
          if (!choiceId) return;
          finish(pending, choiceId);
          return;
        }
        if (options.alternate) {
          finish(pending, "confirm");
          return;
        }
        finish(pending, true);
      }}
    />
  );
};
