import { useState } from "react";

import { Box, Button, Flex, Text } from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";

import {
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";

interface PaymentPageProps {
  invoiceNumber?: string;
  amount?: number;
  onCancel: () => void;
  onSuccess: () => void;
}

const PaymentPage = ({
  invoiceNumber,
  amount,
  onCancel,
  onSuccess,
}: PaymentPageProps) => {
  const stripe = useStripe();
  const elements = useElements();
  const [processing, setProcessing] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);

  const handlePay = async () => {
    if (!stripe || !elements) return;

    setProcessing(true);
    setCardError(null);

    try {
      const { error: submitError } = await elements.submit();
      if (submitError) {
        const message = submitError.message || "Payment details are invalid.";
        setCardError(message);
        return;
      }

      const result = await stripe.confirmPayment({
        elements,
        confirmParams: {
          return_url: window.location.href,
        },
        redirect: "if_required",
      });

      if (result.error) {
        const message = result.error.message || "Payment failed.";
        setCardError(message);
        toaster.error({ title: "Payment failed", description: message });
        return;
      }

      if (result.paymentIntent?.status === "succeeded") {
        toaster.success({
          title: "Payment successful",
          description: "Your invoice has been paid.",
        });
        onSuccess();
        return;
      }

      toaster.info({
        title: "Payment processing",
        description: "Your payment is being processed.",
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Payment failed.";
      setCardError(message);
      toaster.error({ title: "Payment failed", description: message });
    } finally {
      setProcessing(false);
    }
  };

  return (
    <Box>
      <Text fontSize="lg" fontWeight="700" mb={1}>
        Pay Invoice
      </Text>
      <Text fontSize="sm" color="gray.600" mb={4}>
        {invoiceNumber ? `Invoice ${invoiceNumber}` : "Invoice"}
        {amount !== undefined ? ` • $${Number(amount).toFixed(2)}` : ""}
      </Text>

      <Box
        borderWidth="1px"
        borderColor="gray.200"
        borderRadius="md"
        p={3}
        mb={3}
      >
        <PaymentElement
          options={{
            paymentMethodOrder: ["card"],
            wallets: {
              applePay: "never",
              googlePay: "never",
              link: "never",
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

      <Flex gap={3} justifyContent="flex-end">
        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={processing}
        >
          Cancel
        </Button>
        <Button
          colorPalette="purple"
          size="sm"
          onClick={handlePay}
          loading={processing}
          disabled={!stripe || processing}
        >
          {processing ? "Processing..." : "Pay Now"}
        </Button>
      </Flex>
    </Box>
  );
};

export default PaymentPage;
