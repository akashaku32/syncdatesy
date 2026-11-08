export const confirmCopy = {
  discard: {
    title: "Discard unsaved changes?",
    description: (entity: string) =>
      `You have unsaved changes to the column mapping for ${entity}. If you close now, they will be lost.`,
    cancelLabel: "Keep editing",
    confirmLabel: "Discard changes",
  },
  reset: {
    title: "Reset to the uploaded JSON?",
    description: (restored: number, discardedAdded: number, renames: number) =>
      `This restores ${restored} removed columns and discards ${discardedAdded} added columns and ${renames} renames.`,
    cancelLabel: "Cancel",
    confirmLabel: "Reset mapping",
  },
  removeMapping: {
    title: "Remove this mapping?",
    description:
      "The entity will load all columns with SAP names again. Saved versions are kept.",
    cancelLabel: "Cancel",
    confirmLabel: "Remove mapping",
  },
  replaceJson: {
    title: "Replace the mapping JSON?",
    description:
      "Your manual changes will be kept only where they still apply.",
    cancelLabel: "Cancel",
    confirmLabel: "Keep my changes",
    alternateLabel: "Discard my changes",
  },
  replaceScript: {
    title: "Replace the script?",
    description: "Unsaved edits in the script editor will be replaced.",
    cancelLabel: "Cancel",
    confirmLabel: "Replace script",
  },
  insertExample: {
    title: "Insert example?",
    description: "This adds the example to the end of the script body.",
    cancelLabel: "Cancel",
    confirmLabel: "Insert",
  },
  removeScript: {
    title: "Remove the script?",
    description: "The transformation body will be cleared.",
    cancelLabel: "Cancel",
    confirmLabel: "Remove script",
  },
  replaceMapping: {
    title: "Replace this mapping?",
    description: (currentSource: string, nextSource: string, target: string) =>
      `Replace ${currentSource} → ${target} with ${nextSource} → ${target}?`,
    cancelLabel: "Cancel",
    confirmLabel: "Replace mapping",
  },
  renameDestination: {
    title: "Rename the destination table?",
    description: (previous: string, next: string) =>
      `Data was already loaded into ${previous}. Destination is now ${next}. Choose how to proceed.`,
    plainDescription: (current: string, next: string) =>
      `Rename destination table from ${current} to ${next}?`,
    cancelLabel: "Cancel",
    confirmLabel: "Continue",
    renameLabel: "Rename table",
    choices: [
      { id: "create_new", label: "Create new table and full reload" },
      { id: "rename_existing", label: "Rename existing table" },
    ],
  },
  addColumns: {
    title: "Add columns to loaded data?",
    description:
      "Data was already loaded for this entity. Choose whether to reload the table or add the new columns only.",
    cancelLabel: "Cancel",
    confirmLabel: "Continue",
    choices: [
      { id: "full_reload", label: "Full reload" },
      { id: "new_columns_only", label: "New columns only" },
    ],
  },
  saveWithoutTest: {
    title: "Validate without a dry run?",
    description:
      "The mapping and script have not been dry-run tested. Validate still checks syntax and mapping; runtime errors may appear when the pipeline runs.",
    cancelLabel: "Go back",
    confirmLabel: "Validate",
  },
  restoreDraft: {
    title: "Restore local draft?",
    description:
      "A draft of this mapping is saved in this browser (in addition to any server draft).",
    cancelLabel: "Discard local draft",
    confirmLabel: "Restore draft",
  },
  saved: (version: number) => `Draft saved (version ${version})`,
  published: (version: number) => `Validated version ${version}`,
  validated: (version: number) => `Validated v${version}`,
  draftBadge: "Draft",
  validatedBadge: (version: number) => `Validated v${version}`,
  removed: (name: string) => `${name} removed`,
  copied: "Copied",
  scriptValidated: "Python syntax valid",
  scriptSavedAndValidated: "Script validated",
  retry: "Retry",
};
