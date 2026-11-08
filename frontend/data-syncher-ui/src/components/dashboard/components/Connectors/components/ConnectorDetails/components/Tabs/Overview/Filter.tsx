import { NativeSelect } from "@chakra-ui/react";

const Filter = ({
  filterDays,
  setFilterDays,
}: {
  filterDays: number;
  setFilterDays: (_days: number) => void;
}) => {
  return (
    <NativeSelect.Root size="sm" width="145px">
      <NativeSelect.Field
        value={String(filterDays)}
        onChange={(e) => setFilterDays(Number(e.currentTarget.value))}
      >
        {/* Placeholder option - disabled so it cannot be selected/clicked */}
        <option value="" disabled hidden>
          Select option
        </option>
        <option value="2">Last 2 days</option>
        <option value="7">Last 7 days</option>
        <option value="30">Last 30 days</option>
      </NativeSelect.Field>
      <NativeSelect.Indicator />
    </NativeSelect.Root>
  );
};

export default Filter;
