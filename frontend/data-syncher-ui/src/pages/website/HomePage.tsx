import { useEffect } from "react";

import {
  Box,
  Button,
  Container,
  Grid,
  Heading,
  Image,
  Stack,
  Text,
} from "@chakra-ui/react";

import { FiArrowRight } from "react-icons/fi";

import { useLocation, useNavigate } from "react-router";

import LogoWhite from "@/assets/images/logo-white.svg";
import ConnectorLogoStrip from "@/components/website/ConnectorLogoStrip";
import HomeFeaturesSection from "@/components/website/HomeFeaturesSection";
import HomeHeroVisual from "@/components/website/HomeHeroVisual";
import HomeHowItWorksSection from "@/components/website/HomeHowItWorksSection";
import { APP_NAME, APP_TAGLINE } from "@/constants/branding";
import { CTA_GRADIENT, HERO_GRADIENT } from "@/constants/marketing-visual";
import ClientRoutes from "@/constants/client-routes";

export default function HomePage() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const state = location.state as { scrollTo?: string } | null;
    const targetId = state?.scrollTo || location.hash.replace("#", "");
    if (!targetId) return;

    const timer = window.setTimeout(() => {
      document.getElementById(targetId)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }, 80);

    return () => window.clearTimeout(timer);
  }, [location.hash, location.state]);

  const goLogin = () =>
    navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`);
  const goRegister = () =>
    navigate(`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`);

  return (
    <Box w="full" bg="bg.canvas" scrollMarginTop="5rem">
      <Box
        id="home"
        position="relative"
        overflow="hidden"
        pt={{ base: 24, md: 28 }}
        pb={{ base: 16, md: 20 }}
        color="white"
        bg="navy.950"
        backgroundImage={HERO_GRADIENT}
      >
        <Box
          aria-hidden
          position="absolute"
          top="-15%"
          right="5%"
          w="480px"
          h="480px"
          borderRadius="full"
          bg="rgba(45, 212, 191, 0.2)"
          filter="blur(80px)"
        />
        <Box
          aria-hidden
          position="absolute"
          bottom="-20%"
          left="-10%"
          w="400px"
          h="400px"
          borderRadius="full"
          bg="rgba(14, 165, 233, 0.1)"
          filter="blur(72px)"
        />
        <Image
          aria-hidden
          src={LogoWhite}
          alt=""
          position="absolute"
          right={{ base: "-20%", lg: "8%" }}
          bottom={{ base: "-10%", lg: "5%" }}
          w={{ base: "280px", lg: "360px" }}
          opacity={0.12}
          pointerEvents="none"
        />

        <Container maxW="container.xl" position="relative" zIndex={1}>
          <Grid
            templateColumns={{ base: "1fr", lg: "1.05fr 1fr" }}
            gap={{ base: 12, lg: 16 }}
            alignItems="center"
          >
            <Stack gap={6}>
              <Text
                fontSize="xs"
                fontWeight="bold"
                letterSpacing="0.14em"
                textTransform="uppercase"
                color="brand.200"
              >
                Built for modern data teams
              </Text>
              <Heading
                as="h1"
                fontSize={{ base: "3xl", md: "4.5xl", lg: "5xl" }}
                lineHeight="1.08"
                letterSpacing="-0.03em"
                fontWeight="bold"
              >
                Sync once.{" "}
                <Box as="span" color="brand.300">
                  Trust always.
                </Box>
              </Heading>
              <Text
                fontSize={{ base: "md", md: "lg" }}
                color="whiteAlpha.900"
                maxW="xl"
                lineHeight="tall"
              >
                {APP_TAGLINE} Orchestrate {APP_NAME} connectors, migrations,
                and batch jobs from one control plane.
              </Text>
              <Stack direction={{ base: "column", sm: "row" }} gap={3}>
                <Button
                  size="lg"
                  colorPalette="brand"
                  onClick={goLogin}
                  boxShadow="0 12px 28px -8px rgba(13, 148, 136, 0.55)"
                >
                  Sign in <FiArrowRight />
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  borderColor="whiteAlpha.500"
                  color="white"
                  _hover={{ bg: "whiteAlpha.150" }}
                  onClick={goRegister}
                >
                  Request access
                </Button>
              </Stack>
            </Stack>
            <HomeHeroVisual />
          </Grid>
        </Container>
      </Box>

      <ConnectorLogoStrip />

      <HomeFeaturesSection />

      <HomeHowItWorksSection />

      <Box
        id="contact"
        py={{ base: 14, md: 18 }}
        scrollMarginTop="5.5rem"
      >
        <Container maxW="container.lg">
          <Stack
            gap={5}
            textAlign="center"
            p={{ base: 8, md: 14 }}
            borderRadius="2xl"
            color="white"
            position="relative"
            overflow="hidden"
            backgroundImage={CTA_GRADIENT}
            boxShadow="0 24px 48px -20px rgba(13, 148, 136, 0.4)"
          >
            <Box
              aria-hidden
              position="absolute"
              inset={0}
              backgroundImage={`url("${LogoWhite}")`}
              backgroundRepeat="no-repeat"
              backgroundPosition="right -40px bottom -60px"
              backgroundSize="320px"
              opacity={0.15}
            />
            <Stack gap={2} position="relative">
              <Heading size="xl" letterSpacing="-0.02em">
                Ready to explore Syncdatesy?
              </Heading>
              <Text color="whiteAlpha.900" maxW="lg" mx="auto">
                Sign in with your organization account or request access for
                your team.
              </Text>
            </Stack>
            <Stack
              direction={{ base: "column", sm: "row" }}
              gap={3}
              justify="center"
              position="relative"
            >
              <Button
                size="lg"
                bg="white"
                color="brand.800"
                _hover={{ bg: "brand.50" }}
                onClick={goLogin}
              >
                Sign in
              </Button>
              <Button
                size="lg"
                variant="outline"
                borderColor="whiteAlpha.700"
                color="white"
                _hover={{ bg: "whiteAlpha.200" }}
                onClick={goRegister}
              >
                Request access
              </Button>
            </Stack>
          </Stack>
        </Container>
      </Box>
    </Box>
  );
}
