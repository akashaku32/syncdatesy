import { useEffect, useState } from "react";

import { Text } from "@chakra-ui/react";

import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import PaymentPage from "./PaymentPage";
import { Elements } from "@stripe/react-stripe-js";
import { type StripeElementsOptions, loadStripe } from "@stripe/stripe-js";

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

interface PaymentWrapperProps {
  invoiceId: number;
  invoiceNumber?: string;
  amount?: number;
  onCancel: () => void;
  onSuccess: () => void;
}

const PaymentWrapper = ({
  invoiceId,
  invoiceNumber,
  amount,
  onCancel,
  onSuccess,
}: PaymentWrapperProps) => {
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isActive = true;
    const fetchClientSecret = async () => {
      setLoading(true);
      setError(null);
      try {
        const { data } = await AxiosInstance.post(
          ServerRoutes.billing.createPaymentIntent({ id: invoiceId }),
          {},
        );
        const secret = String(data?.client_secret || "");
        if (!secret) {
          throw new Error("Missing client secret.");
        }
        if (isActive) {
          setClientSecret(secret);
        }
      } catch (err) {
        if (isActive) {
          const message =
            err instanceof Error ? err.message : "Unable to start payment.";
          setError(message);
        }
      } finally {
        if (isActive) setLoading(false);
      }
    };

    fetchClientSecret();
    return () => {
      isActive = false;
    };
  }, [invoiceId]);

  if (!stripePromise) {
    return (
      <Text color="red.500" fontSize="sm">
        Stripe publishable key is missing. Set{" "}
        <Text as="span" fontWeight="600">
          VITE_STRIPE_PUBLISHABLE_KEY
        </Text>{" "}
        in your environment.
      </Text>
    );
  }

  if (loading) {
    return <Text fontSize="sm">Preparing payment...</Text>;
  }

  if (error || !clientSecret) {
    return (
      <Text color="red.500" fontSize="sm">
        {error || "Unable to start payment."}
      </Text>
    );
  }

  const elementsOptions: StripeElementsOptions = {
    clientSecret,
    appearance: {
      theme: "stripe",
    },
  };

  return (
    <Elements stripe={stripePromise} options={elementsOptions}>
      <PaymentPage
        invoiceNumber={invoiceNumber}
        amount={amount}
        onCancel={onCancel}
        onSuccess={onSuccess}
      />
    </Elements>
  );
};

export default PaymentWrapper;
