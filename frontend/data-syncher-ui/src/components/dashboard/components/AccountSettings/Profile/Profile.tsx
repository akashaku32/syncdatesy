import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  Box,
  Button,
  Checkbox,
  Field,
  Flex,
  Input,
  Portal,
  Select,
  SimpleGrid,
  Text,
  Textarea,
  createListCollection,
} from "@chakra-ui/react";

import { MdOutlineEdit } from "react-icons/md";

import { format } from "date-fns";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import useFetchAccountCountries from "@/queryOptions/account/useFetchAccountCountries";
import useFetchAccountProfile from "@/queryOptions/account/useFetchAccountProfile";
import useUpdateAccountProfile from "@/queryOptions/account/useUpdateAccountProfile";
import useCreateSetupIntent from "@/queryOptions/billing/useCreateSetupIntent";
import {
  type AccountAddress,
  type AccountBillingDetails,
} from "@/types/accountProfile";

import {
  Elements,
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";
import { type StripeElementsOptions, loadStripe } from "@stripe/stripe-js";

const formatDateTime = (value?: string | null) => {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return format(d, "MMM dd, yyyy, hh:mm:ss a");
};

const pickString = (...values: Array<unknown>) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number") return String(value);
  }
  return "—";
};

const pickOptionalString = (...values: Array<unknown>) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number") return String(value);
  }
  return null;
};

const isMeaningfulValue = (value?: string | null) => {
  if (!value) return false;
  const normalized = value.trim();
  if (!normalized) return false;
  return normalized !== "â€”" && normalized !== "-" && normalized !== "—";
};

const formatMaskedCardNumber = (last4?: string | null) => {
  if (!last4) return null;
  const digits = String(last4).trim();
  if (!digits) return null;
  if (digits.includes("*")) return digits;
  return `**** ${digits}`;
};

const pickNumber = (...values: Array<unknown>) => {
  for (const value of values) {
    if (typeof value === "number" && !Number.isNaN(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (!Number.isNaN(parsed)) return parsed;
    }
  }
  return null;
};

const pickBoolean = (...values: Array<unknown>) => {
  for (const value of values) {
    if (typeof value === "boolean") return value;
  }
  return null;
};

const normalizeCountryList = (payload: unknown) => {
  const list = Array.isArray(payload) ? payload : [];
  const normalized = list
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (typeof item === "object" && item) {
        const obj = item as Record<string, unknown>;
        return (
          pickOptionalString(
            obj.country_name,
            obj.Country_name,
            obj.name,
            obj.country,
            obj.label,
            obj.value,
          ) ?? ""
        ).trim();
      }
      return "";
    })
    .filter((value) => value);

  const seen = new Set<string>();
  return normalized.filter((value) => {
    if (seen.has(value)) return false;
    seen.add(value);
    return true;
  });
};

const normalizeExpiryPart = (value: unknown) => {
  if (typeof value === "number" && !Number.isNaN(value)) return String(value);
  if (typeof value === "string" && value.trim()) return value.trim();
  return null;
};

const formatExpiryFromParts = (month: unknown, year: unknown) => {
  const m = normalizeExpiryPart(month);
  const y = normalizeExpiryPart(year);
  if (!m || !y) return null;
  const monthText = /^\d+$/.test(m) ? m.padStart(2, "0") : m;
  const yearText = /^\d+$/.test(y) ? y : y;
  return `${monthText}/${yearText}`;
};

const SectionCard = ({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) => (
  <Box
    borderWidth="1px"
    borderColor={{ _light: "gray.200", _dark: "gray.700" }}
    borderRadius="lg"
    p={3}
    bg={{ _light: "white", _dark: "gray.800" }}
  >
    <Flex justifyContent="space-between" alignItems="center" mb={3}>
      <Text fontWeight="semibold">{title}</Text>
      {action}
    </Flex>
    {children}
  </Box>
);

const ReadOnlyField = ({ label, value }: { label: string; value: string }) => (
  <Field.Root>
    <Field.Label fontSize="xs">{label}</Field.Label>
    <Input
      size="sm"
      value={value}
      readOnly
      bg={{ _light: "gray.100", _dark: "gray.700" }}
      borderColor={{ _light: "gray.200", _dark: "gray.600" }}
    />
  </Field.Root>
);

const PaymentMethodForm = ({
  onSuccess,
  onCancel,
  setupClientSecret,
}: {
  onSuccess: () => void;
  onCancel: () => void;
  setupClientSecret: string;
}) => {
  const stripe = useStripe();
  const elements = useElements();
  const [processing, setProcessing] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);
  const [cardholderName, setCardholderName] = useState("");

  const handleSave = async () => {
    if (!stripe || !elements) return;

    setProcessing(true);
    setCardError(null);

    try {
      if (!setupClientSecret) {
        setCardError("Unable to start card setup.");
        return;
      }
      const normalizedName = cardholderName.trim();
      if (!normalizedName) {
        setCardError("Card holder name is required.");
        return;
      }
      if (normalizedName.length < 2) {
        setCardError("Card holder name must be at least 2 characters.");
        return;
      }
      const { error: submitError } = await elements.submit();
      if (submitError) {
        const message = submitError.message || "Card details are invalid.";
        setCardError(message);
        return;
      }

      const result = await stripe.confirmSetup({
        elements,
        confirmParams: {
          return_url: window.location.href,
          payment_method_data: {
            billing_details: {
              name: normalizedName,
            },
          },
        },
        redirect: "if_required",
      });

      if (result.error) {
        const message = result.error.message || "Card setup failed.";
        setCardError(message);
        toaster.error({ title: "Card setup failed", description: message });
        return;
      }

      toaster.success({
        title: "Card saved",
        description: "Your default payment method has been updated.",
      });
      onSuccess();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Card setup failed.";
      setCardError(message);
      toaster.error({ title: "Card setup failed", description: message });
    } finally {
      setProcessing(false);
    }
  };

  return (
    <Box>
      <Field.Root mb={3}>
        <Field.Label fontSize="xs">Card holder name</Field.Label>
        <Box
          borderWidth="1px"
          borderColor={{ _light: "gray.200", _dark: "gray.700" }}
          borderRadius="md"
          px={3}
          py={2}
          minH="44px"
          display="flex"
          alignItems="center"
          bg={{ _light: "white", _dark: "gray.800" }}
          w="100%"
        >
          <Input
            size="sm"
            border="none"
            outline="none"
            boxShadow="none"
            borderColor="transparent"
            _focus={{ borderColor: "transparent", boxShadow: "none" }}
            _hover={{ borderColor: "transparent" }}
            px={0}
            py={0}
            h="100%"
            bg="transparent"
            _focusVisible={{ boxShadow: "none" }}
            placeholder="Name as shown on card"
            value={cardholderName}
            onChange={(event) => setCardholderName(event.target.value)}
            w="100%"
          />
        </Box>
      </Field.Root>
      <Box
        borderWidth="1px"
        borderColor={{ _light: "gray.200", _dark: "gray.700" }}
        borderRadius="md"
        p={3}
        mb={3}
        bg={{ _light: "white", _dark: "gray.800" }}
      >
        <PaymentElement
          options={{
            paymentMethodOrder: ["card"],
            wallets: {
              applePay: "never",
              googlePay: "never",
              link: "never",
            },
            fields: {
              billingDetails: {
                name: "never",
              },
            },
          }}
          onChange={(event) => {
            if (event.complete) {
              setCardError(null);
            }
          }}
        />
      </Box>

      {cardError && (
        <Text fontSize="sm" color="red.500" mb={3}>
          {cardError}
        </Text>
      )}

      <Flex justifyContent="flex-end" gap={2}>
        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={processing}
        >
          Cancel
        </Button>
        <Button
          colorPalette="brand"
          size="sm"
          onClick={handleSave}
          loading={processing}
          disabled={!stripe || processing}
        >
          {processing ? "Saving..." : "Save card"}
        </Button>
      </Flex>
    </Box>
  );
};

type AddressFormState = {
  companyName: string;
  phone: string;
  address1: string;
  address2: string;
  city: string;
  state: string;
  zipCode: string;
  country: string;
};

const AddressForm = ({
  value,
  onChange,
  countries,
  disabled = false,
}: {
  value: AddressFormState;
  onChange: (_field: keyof AddressFormState, _nextValue: string) => void;
  countries: string[];
  disabled?: boolean;
}) => {
  const [countryQuery, setCountryQuery] = useState("");
  const countryOptions =
    value.country && !countries.includes(value.country)
      ? [value.country, ...countries]
      : countries;
  const filteredCountries = countryOptions.filter((country) =>
    country.toLowerCase().includes(countryQuery.trim().toLowerCase()),
  );
  const countryItems = filteredCountries.map((country) => ({
    label: country,
    value: country,
  }));
  const countryCollection = createListCollection({
    items: countryItems,
  });
  return (
    <SimpleGrid columns={{ base: 1, md: 2 }} gap={1}>
      <Field.Root w="100%">
        <Field.Label fontSize="xs">Company name</Field.Label>
        <Input
          size="sm"
          placeholder="Enter company name"
          h="36px"
          px={3}
          py={2}
          value={value.companyName}
          onChange={(e) => onChange("companyName", e.target.value)}
          disabled
        />
      </Field.Root>
      <Field.Root w="100%">
        <Field.Label fontSize="xs">Phone</Field.Label>
        <Input
          size="sm"
          placeholder="Enter phone number"
          h="36px"
          px={3}
          py={2}
          value={value.phone}
          onChange={(e) => onChange("phone", e.target.value)}
          disabled={disabled}
        />
      </Field.Root>
      <Field.Root gridColumn={{ base: "auto", md: "span 2" }} w="100%">
        <Field.Label fontSize="xs">Address line 1</Field.Label>
        <Textarea
          size="sm"
          rows={1}
          resize="vertical"
          placeholder="Enter address line 1"
          minH="36px"
          px={3}
          py={2}
          value={value.address1}
          onChange={(e) => onChange("address1", e.target.value)}
          disabled={disabled}
        />
      </Field.Root>
      <Field.Root gridColumn={{ base: "auto", md: "span 2" }} w="100%">
        <Field.Label fontSize="xs">Address line 2</Field.Label>
        <Textarea
          size="sm"
          rows={1}
          resize="vertical"
          placeholder="Enter address line 2"
          minH="36px"
          px={3}
          py={2}
          value={value.address2}
          onChange={(e) => onChange("address2", e.target.value)}
          disabled={disabled}
        />
      </Field.Root>
      <Field.Root w="100%">
        <Field.Label fontSize="xs">City</Field.Label>
        <Input
          size="sm"
          placeholder="Enter city"
          h="36px"
          px={3}
          py={2}
          value={value.city}
          onChange={(e) => onChange("city", e.target.value)}
          disabled={disabled}
        />
      </Field.Root>
      <Field.Root w="100%">
        <Field.Label fontSize="xs">Zip code</Field.Label>
        <Input
          size="sm"
          placeholder="Enter zip code"
          h="36px"
          px={3}
          py={2}
          value={value.zipCode}
          onChange={(e) => onChange("zipCode", e.target.value)}
          disabled={disabled}
        />
      </Field.Root>
      <Field.Root w="100%">
        <Field.Label fontSize="xs">State</Field.Label>
        <Input
          size="sm"
          placeholder="Enter state"
          h="36px"
          px={3}
          py={2}
          value={value.state}
          onChange={(e) => onChange("state", e.target.value)}
          disabled={disabled}
        />
      </Field.Root>
      <Field.Root w="100%">
        <Field.Label fontSize="xs">Country</Field.Label>
        <Select.Root
          collection={countryCollection}
          value={value.country ? [value.country] : []}
          onValueChange={({ value: next }) => {
            onChange("country", next[0] ?? "");
          }}
          disabled={disabled}
        >
          <Select.HiddenSelect />
          <Select.Control minH="36px" h="36px" w="100%">
            <Select.Trigger minH="36px" h="36px" px={3} py={2} w="100%">
              <Select.ValueText placeholder="Select country" />
            </Select.Trigger>
            <Select.IndicatorGroup>
              <Select.Indicator />
            </Select.IndicatorGroup>
          </Select.Control>
          <Portal>
            <Select.Positioner>
              <Select.Content>
                <Box p={2}>
                  <Input
                    size="sm"
                    placeholder="Search country..."
                    value={countryQuery}
                    onChange={(e) => setCountryQuery(e.target.value)}
                  />
                </Box>
                {countryCollection.items.map((item) => (
                  <Select.Item item={item} key={item.value}>
                    {item.label}
                    <Select.ItemIndicator />
                  </Select.Item>
                ))}
                {countryCollection.items.length === 0 ? (
                  <Box px={3} py={2} fontSize="sm" color="gray.500">
                    No results
                  </Box>
                ) : null}
              </Select.Content>
            </Select.Positioner>
          </Portal>
        </Select.Root>
      </Field.Root>
    </SimpleGrid>
  );
};

const normalizeBilling = (
  raw: Record<string, unknown> | null,
  billing: AccountBillingDetails | null | undefined,
) => {
  const source = (billing ?? (raw?.billing as Record<string, unknown>)) || {};
  const billingSource = source as Record<string, unknown>;
  const last4 =
    pickOptionalString(
      billingSource.card_last4,
      billingSource.last4,
      billingSource.stripe_card_last4,
      raw?.stripe_card_last4,
      raw?.card_last4,
    ) ?? null;
  const cardBrand =
    pickOptionalString(
      billingSource.card_brand,
      billingSource.brand,
      billingSource.stripe_card_brand,
      raw?.stripe_card_brand,
      raw?.card_brand,
    ) ?? null;
  const expMonth =
    pickOptionalString(
      billingSource.card_exp_month,
      billingSource.exp_month,
      billingSource.stripe_card_exp_month,
      raw?.stripe_card_exp_month,
    ) ?? null;
  const expYear =
    pickOptionalString(
      billingSource.card_exp_year,
      billingSource.exp_year,
      billingSource.stripe_card_exp_year,
      raw?.stripe_card_exp_year,
    ) ?? null;
  const stripeExpiry = formatExpiryFromParts(expMonth, expYear) ?? null;
  return {
    cardNumber: pickString(
      billingSource.card_number,
      billingSource.card_no,
      billingSource.credit_card_number,
      billingSource.stripe_card_last4,
      formatMaskedCardNumber(last4),
      raw?.card_number,
      raw?.stripe_card_last4,
    ),
    cardHolder: pickString(
      billingSource.card_holder_name,
      billingSource.card_holder,
      billingSource.holder_name,
      billingSource.stripe_card_holder_name,
      raw?.stripe_card_holder_name,
      raw?.card_holder_name,
    ),
    cardExpiry: pickString(
      billingSource.card_expiry,
      billingSource.valid_through,
      billingSource.expiry,
      billingSource.expiry_date,
      stripeExpiry,
      raw?.card_expiry,
      raw?.expiry,
    ),
    cardBrand: pickString(cardBrand),
    cardLast4: pickString(last4),
    nextBillingDate: pickString(
      billingSource.next_billing_date,
      billingSource.next_billing,
      billingSource.next_invoice_date,
      raw?.next_billing_date,
    ),
  };
};

const normalizeAddress = (
  raw: Record<string, unknown> | null,
  address: AccountAddress | null | undefined,
  kind: "billing" | "shipping",
): AddressFormState => {
  const rawSource =
    (raw?.[`${kind}_address`] as Record<string, unknown> | null) ??
    (raw?.[kind] as Record<string, unknown> | null)?.address ??
    null;
  const source =
    (address as Record<string, unknown> | null) ??
    rawSource ??
    (raw?.address as Record<string, unknown> | null) ??
    {};
  const addressSource = source as Record<string, unknown>;
  const companyRecord =
    (raw?.company as Record<string, unknown> | null) ?? null;
  return {
    companyName:
      pickOptionalString(
        addressSource.company_name,
        raw?.company_name,
        raw?.cmp_name,
        companyRecord?.name,
        companyRecord?.cmp_name,
      ) ?? "",
    phone: pickOptionalString(addressSource.phone) ?? "",
    address1: pickOptionalString(addressSource.address1) ?? "",
    address2: pickOptionalString(addressSource.address2) ?? "",
    city: pickOptionalString(addressSource.city) ?? "",
    state: pickOptionalString(addressSource.state) ?? "",
    zipCode: pickOptionalString(addressSource.zip_code) ?? "",
    country: pickOptionalString(addressSource.country) ?? "",
  };
};

const toApiAddress = (address: AddressFormState): AccountAddress => ({
  company_name: address.companyName.trim() || null,
  phone: address.phone.trim() || null,
  address1: address.address1.trim() || null,
  address2: address.address2.trim() || null,
  city: address.city.trim() || null,
  state: address.state.trim() || null,
  zip_code: address.zipCode.trim() || null,
  country: address.country.trim() || null,
});

const stripeKey = import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY;
const stripePromise = stripeKey
  ? loadStripe(stripeKey, {
      developerTools: {
        assistant: {
          enabled: false,
        },
      },
    })
  : null;

const AccountProfile = () => {
  const { data, isLoading, refetch: refetchProfile } = useFetchAccountProfile();
  const { data: countriesData } = useFetchAccountCountries();
  const { mutate: updateAccountProfile, isPending: isSaving } =
    useUpdateAccountProfile();
  const { mutateAsync: createSetupIntent, isPending: isCreatingSetupIntent } =
    useCreateSetupIntent();

  const raw = (data ?? null) as Record<string, unknown> | null;
  const countryOptions = useMemo(
    () => normalizeCountryList(countriesData),
    [countriesData],
  );
  const billingAddressInitial = useMemo(
    () => normalizeAddress(raw, data?.billing_address, "billing"),
    [raw, data?.billing_address],
  );
  const shippingAddressInitial = useMemo(
    () => normalizeAddress(raw, data?.shipping_address, "shipping"),
    [raw, data?.shipping_address],
  );
  const [billingAddress, setBillingAddress] = useState<AddressFormState>(
    billingAddressInitial,
  );
  const [shippingAddress, setShippingAddress] = useState<AddressFormState>(
    shippingAddressInitial,
  );
  const [isSameAsBilling, setIsSameAsBilling] = useState(false);
  const [setupClientSecret, setSetupClientSecret] = useState<string | null>(
    null,
  );
  const [setupError, setSetupError] = useState<string | null>(null);
  const [showCardForm, setShowCardForm] = useState(false);
  const [hasCompletedSetup, setHasCompletedSetup] = useState(false);

  useEffect(() => {
    setBillingAddress(billingAddressInitial);
  }, [billingAddressInitial]);

  useEffect(() => {
    setShippingAddress(shippingAddressInitial);
  }, [shippingAddressInitial]);

  useEffect(() => {
    if (isSameAsBilling) {
      setShippingAddress(billingAddress);
    }
  }, [isSameAsBilling, billingAddress]);

  const companyName = pickString(
    data?.company_name,
    data?.company?.name,
    data?.company?.cmp_name,
    raw?.company_name,
    raw?.cmp_name,
  );
  const createdAt = pickString(
    data?.account_created_at,
    data?.company?.created_at,
    raw?.account_created_at,
    raw?.created_at,
  );
  const createdByRecord =
    (raw?.created_by as Record<string, unknown> | null) ?? null;
  const createdByName = [
    pickOptionalString(createdByRecord?.first_name),
    pickOptionalString(createdByRecord?.last_name),
  ]
    .filter(Boolean)
    .join(" ");
  const createdBy = createdByName
    ? createdByName
    : pickString(data?.created_by, data?.company?.created_by, raw?.created_by);

  const accountStatus = pickString(
    data?.account_status,
    data?.subscription?.status,
    data?.company?.status,
    raw?.account_status,
    raw?.status,
  );
  const validFrom = pickString(
    data?.company?.valid_from,
    raw?.valid_from,
    raw?.valid_from_date,
  );
  const validTo = pickString(
    data?.company?.valid_to,
    raw?.valid_to,
    raw?.valid_to_date,
  );

  const isTrial = pickBoolean(
    data?.is_trial,
    data?.subscription?.is_trial,
    raw?.is_trial,
  );
  const planName = pickString(
    raw?.plan,
    (raw?.subscription as Record<string, unknown> | null)?.plan,
  );
  const trialDays = pickNumber(
    data?.trial_days_remaining,
    data?.subscription?.trial_days_remaining,
    raw?.trial_days_remaining,
    raw?.trial_days_left,
  );

  const billingDetails = normalizeBilling(raw, data?.billing);
  const showBilling = isTrial === false;
  const hasSavedCard =
    isMeaningfulValue(billingDetails.cardBrand) ||
    isMeaningfulValue(billingDetails.cardExpiry) ||
    isMeaningfulValue(billingDetails.cardLast4) ||
    isMeaningfulValue(billingDetails.cardNumber) ||
    isMeaningfulValue(billingDetails.cardHolder);

  useEffect(() => {
    if (!showBilling) return;
    if (hasSavedCard) {
      setShowCardForm(false);
      setHasCompletedSetup(false);
    }
  }, [showBilling, hasSavedCard]);

  useEffect(() => {
    if (!showBilling) return;
    if (!stripePromise) return;
    if (!showCardForm) return;
    if (setupClientSecret || isCreatingSetupIntent) return;

    const loadSetupIntent = async () => {
      try {
        setSetupError(null);
        const result = await createSetupIntent();
        setSetupClientSecret(result.clientSecret);
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Unable to start card setup.";
        setSetupError(message);
      }
    };

    loadSetupIntent();
  }, [
    showBilling,
    showCardForm,
    setupClientSecret,
    isCreatingSetupIntent,
    createSetupIntent,
  ]);

  if (isLoading) return <LoadingSpinner />;

  const updateAddress =
    (setter: Dispatch<SetStateAction<AddressFormState>>) =>
    (field: keyof AddressFormState, nextValue: string) =>
      setter((prev) => ({ ...prev, [field]: nextValue }));
  const handleBillingChange = updateAddress(setBillingAddress);
  const handleShippingChange = updateAddress(setShippingAddress);
  const handleSameAsBillingChange = (checked: boolean) => {
    setIsSameAsBilling(checked);
    if (checked) {
      setShippingAddress(billingAddress);
    }
  };
  const handleSaveAddresses = () => {
    updateAccountProfile(
      {
        billing_address: toApiAddress(billingAddress),
        shipping_address: toApiAddress(shippingAddress),
      },
      {
        onSuccess: (response) => {
          toaster.success({
            title: "Account profile updated",
            description: "Billing and shipping addresses have been saved.",
          });
          const responseRaw = (response ?? null) as Record<
            string,
            unknown
          > | null;
          setBillingAddress(
            normalizeAddress(responseRaw, response.billing_address, "billing"),
          );
          setShippingAddress(
            normalizeAddress(
              responseRaw,
              response.shipping_address,
              "shipping",
            ),
          );
        },
        onError: (error) => {
          toaster.error({
            title: "Save failed",
            description: error.message || "Unable to save addresses.",
          });
        },
      },
    );
  };

  const handleRetrySetupIntent = async () => {
    try {
      setSetupError(null);
      const result = await createSetupIntent();
      setSetupClientSecret(result.clientSecret);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unable to start card setup.";
      setSetupError(message);
    }
  };

  const getCardSignature = (
    profile: typeof data | null | undefined,
  ): string | null => {
    if (!profile) return null;
    const profileRaw = (profile ?? null) as Record<string, unknown> | null;
    const profileBilling = normalizeBilling(profileRaw, profile.billing);
    const parts = [
      profileBilling.cardBrand,
      profileBilling.cardNumber,
      profileBilling.cardExpiry,
      profileBilling.cardHolder,
    ]
      .map((value) => (isMeaningfulValue(value) ? value : null))
      .filter(Boolean);
    return parts.length ? parts.join("|") : null;
  };

  const handleCardSaved = async () => {
    const currentSignature = getCardSignature(data);
    await refetchProfile();
    setSetupClientSecret(null);
    setShowCardForm(false);
    setHasCompletedSetup(true);

    const maxAttempts = 10;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const result = await refetchProfile();
      const nextSignature = getCardSignature(result.data);
      if (nextSignature && nextSignature !== currentSignature) {
        setHasCompletedSetup(false);
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  };

  const elementsOptions: StripeElementsOptions | null = setupClientSecret
    ? {
        clientSecret: setupClientSecret,
        appearance: {
          theme: "stripe",
        },
      }
    : null;

  return (
    <Flex flexDirection="column" gap={3} h="100%">
      <PageHeader
        breadcrumbs={[
          {
            label: "Account Settings",
            route: "",
          },
        ]}
        title="Account Profile"
        subtitle="Company and subscription details"
      />

      <SimpleGrid columns={{ base: 1, lg: 2 }} gap={3}>
        <SectionCard title="Company Details">
          <SimpleGrid columns={{ base: 1, md: 2 }} gap={2}>
            <ReadOnlyField label="Company name" value={companyName} />
            <ReadOnlyField
              label="Account created date"
              value={formatDateTime(createdAt)}
            />
            <ReadOnlyField label="Created by" value={createdBy} />
            <ReadOnlyField label="Account status" value={accountStatus} />
            <ReadOnlyField
              label="Valid from"
              value={formatDateTime(validFrom)}
            />
            <ReadOnlyField label="Valid to" value={formatDateTime(validTo)} />
          </SimpleGrid>
        </SectionCard>

        <SectionCard title="Subscription">
          <SimpleGrid columns={{ base: 1, md: 2 }} gap={2}>
            <ReadOnlyField label="Plan" value={planName} />
            <ReadOnlyField
              label="Plan type"
              value={isTrial === null ? "—" : isTrial ? "Trial" : "Paid"}
            />
            {isTrial ? (
              <ReadOnlyField
                label="Trial days remaining"
                value={`${trialDays ?? 0} days`}
              />
            ) : null}
          </SimpleGrid>
        </SectionCard>
      </SimpleGrid>

      {showBilling && (
        <SectionCard title="Addresses">
          <SimpleGrid columns={{ base: 1, lg: 2 }} gap={2}>
            <Box
              borderWidth="1px"
              borderColor={{ _light: "gray.200", _dark: "gray.700" }}
              borderRadius="md"
              p={2}
              bg={{ _light: "gray.50", _dark: "gray.900" }}
            >
              <Flex alignItems="center" justifyContent="space-between" mb={3}>
                <Text
                  fontSize="sm"
                  fontWeight="semibold"
                  color={{ _light: "gray.600", _dark: "gray.300" }}
                >
                  Billing address
                </Text>
              </Flex>
              <AddressForm
                value={billingAddress}
                onChange={handleBillingChange}
                countries={countryOptions}
              />
            </Box>
            <Box
              borderWidth="1px"
              borderColor={{ _light: "gray.200", _dark: "gray.700" }}
              borderRadius="md"
              p={2}
              bg={{ _light: "gray.50", _dark: "gray.900" }}
            >
              <Flex alignItems="center" justifyContent="space-between" mb={3}>
                <Text
                  fontSize="sm"
                  fontWeight="semibold"
                  color={{ _light: "gray.600", _dark: "gray.300" }}
                >
                  Shipping address
                </Text>
                <Checkbox.Root
                  size="sm"
                  colorPalette="brand"
                  checked={isSameAsBilling}
                  onCheckedChange={(event) =>
                    handleSameAsBillingChange(!!event.checked)
                  }
                >
                  <Checkbox.HiddenInput />
                  <Checkbox.Control />
                  <Checkbox.Label>Same as billing address</Checkbox.Label>
                </Checkbox.Root>
              </Flex>
              <AddressForm
                value={shippingAddress}
                onChange={handleShippingChange}
                countries={countryOptions}
                disabled={isSameAsBilling}
              />
            </Box>
          </SimpleGrid>
          <Flex
            justifyContent="flex-end"
            mt={2}
            pt={2}
            borderTopWidth="1px"
            borderColor={{ _light: "gray.200", _dark: "gray.700" }}
          >
            <Button
              colorPalette="brand"
              onClick={handleSaveAddresses}
              loading={isSaving}
            >
              Save
            </Button>
          </Flex>
        </SectionCard>
      )}

      {showBilling && (
        <SectionCard
          title="Payment Details"
          action={
            hasSavedCard && !showCardForm ? (
              <Button
                variant="outline"
                colorPalette="brand"
                color="brand.500"
                size="sm"
                onClick={() => setShowCardForm(true)}
              >
                <MdOutlineEdit color="brand.500" />
                Edit
              </Button>
            ) : null
          }
        >
          {hasSavedCard && !showCardForm ? (
            <>
              <SimpleGrid columns={{ base: 1, md: 2 }} gap={2} mb={3}>
                <ReadOnlyField
                  label="Card holder name"
                  value={billingDetails.cardHolder}
                />
                <ReadOnlyField
                  label="Card number"
                  value={billingDetails.cardNumber}
                />
                <ReadOnlyField
                  label="Card brand"
                  value={billingDetails.cardBrand}
                />
                <ReadOnlyField
                  label="Card expiry"
                  value={billingDetails.cardExpiry}
                />
                {billingDetails.nextBillingDate ? (
                  <ReadOnlyField
                    label="Next billing date"
                    value={formatDateTime(billingDetails.nextBillingDate)}
                  />
                ) : null}
              </SimpleGrid>
            </>
          ) : !showCardForm && hasCompletedSetup && !hasSavedCard ? (
            <Flex direction="column" gap={2}>
              <Text fontSize="sm" color="gray.600">
                Card saved. Details will appear shortly.
              </Text>
              <Button
                size="sm"
                variant="outline"
                onClick={() => refetchProfile()}
                alignSelf="flex-start"
              >
                Refresh
              </Button>
            </Flex>
          ) : !showCardForm ? (
            <Flex direction="column" gap={3}>
              <Text fontSize="sm" color="gray.600">
                No payment method saved yet.
              </Text>
              <Button
                size="sm"
                variant="outline"
                alignSelf="flex-start"
                onClick={() => setShowCardForm(true)}
              >
                Add card details
              </Button>
            </Flex>
          ) : !stripePromise ? (
            <Text color="red.500" fontSize="sm">
              Stripe publishable key is missing. Set{" "}
              <Text as="span" fontWeight="600">
                VITE_STRIPE_PUBLISHABLE_KEY
              </Text>{" "}
              in your environment.
            </Text>
          ) : isCreatingSetupIntent && !setupClientSecret ? (
            <Text fontSize="sm">Preparing secure card form...</Text>
          ) : setupError ? (
            <Flex direction="column" gap={2}>
              <Text color="red.500" fontSize="sm">
                {setupError}
              </Text>
              <Button
                size="sm"
                variant="outline"
                onClick={handleRetrySetupIntent}
                loading={isCreatingSetupIntent}
                alignSelf="flex-start"
              >
                Retry
              </Button>
            </Flex>
          ) : elementsOptions ? (
            <Elements stripe={stripePromise} options={elementsOptions}>
              <PaymentMethodForm
                onSuccess={handleCardSaved}
                onCancel={() => setShowCardForm(false)}
                setupClientSecret={setupClientSecret ?? ""}
              />
            </Elements>
          ) : (
            <Text fontSize="sm">Unable to start card setup.</Text>
          )}
        </SectionCard>
      )}
    </Flex>
  );
};

export default AccountProfile;
