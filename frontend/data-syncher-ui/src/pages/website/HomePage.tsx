import {
  Box,
  Button,
  Container,
  Grid,
  Heading,
  SimpleGrid,
  Stack,
  Text,
} from "@chakra-ui/react";

import {
  FiArrowRight,
  FiCloud,
  FiGitBranch,
  FiShield,
  FiZap,
} from "react-icons/fi";

import { useNavigate } from "react-router";

import { APP_NAME, APP_TAGLINE } from "@/constants/branding";
import ClientRoutes from "@/constants/client-routes";

const FEATURES = [
  {
    icon: FiCloud,
    title: "Unified connectors",
    description:
      "Move data between cloud warehouses, CRMs, ERPs, and file stores without bespoke scripts for every pair.",
  },
  {
    icon: FiGitBranch,
    title: "Pipeline orchestration",
    description:
      "Design batch and incremental flows, monitor runs, and resume safely when something needs attention.",
  },
  {
    icon: FiZap,
    title: "Migration engine",
    description:
      "A dedicated migration-function tier handles heavy extract/load work while Django and FastAPI stay responsive.",
  },
  {
    icon: FiShield,
    title: "Proxy & security",
    description:
      "SAP OData and on-prem sources reach the cloud through the proxy agent hub—with mTLS and tenant-scoped access.",
  },
];

const STEPS = [
  {
    step: "01",
    title: "Connect sources & destinations",
    body: "Configure credentials once; Syncdatesy stores them securely for your organization.",
  },
  {
    step: "02",
    title: "Map schemas & schedule",
    body: "Choose tables, transforms, and when pipelines should run—or chain them in the scheduler UI.",
  },
  {
    step: "03",
    title: "Observe & iterate",
    body: "Track progress, activity logs, and failures; adjust mappings without rebuilding the whole stack.",
  },
];

export default function HomePage() {
  const navigate = useNavigate();

  return (
    <Box pt={{ base: 20, md: 24 }} pb={16} w="full">
      <Box
        id="home"
        bgGradient="linear(to-b, brand.50, white)"
        borderBottomWidth="1px"
        borderColor="gray.100"
        py={{ base: 12, md: 20 }}
      >
        <Container maxW="container.xl">
          <Grid
            templateColumns={{ base: "1fr", lg: "1.2fr 1fr" }}
            gap={10}
            alignItems="center"
          >
            <Stack gap={6}>
              <Text
                color="brand.700"
                fontWeight="semibold"
                fontSize="sm"
                letterSpacing="wide"
                textTransform="uppercase"
              >
                Built for modern data teams
              </Text>
              <Heading
                as="h1"
                fontSize={{ base: "3xl", md: "5xl" }}
                lineHeight="shorter"
              >
                {APP_NAME} keeps your data{" "}
                <Box as="span" color="brand.600">
                  in sync
                </Box>
                , end to end
              </Heading>
              <Text
                fontSize={{ base: "md", md: "lg" }}
                color="gray.600"
                maxW="xl"
              >
                {APP_TAGLINE} Orchestrate connectors, migrations, and batch jobs
                from a single workspace backed by the Syncdatesy platform.
              </Text>
              <Stack direction={{ base: "column", sm: "row" }} gap={3}>
                <Button
                  size="lg"
                  colorPalette="brand"
                  onClick={() =>
                    navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
                  }
                >
                  Sign in <FiArrowRight />
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  colorPalette="brand"
                  onClick={() =>
                    navigate(`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`)
                  }
                >
                  Request trial access
                </Button>
              </Stack>
            </Stack>
            <Box
              display={{ base: "none", lg: "block" }}
              borderRadius="2xl"
              p={8}
              bg="white"
              boxShadow="0 24px 64px -24px rgba(15,118,110,0.45)"
              borderWidth="1px"
              borderColor="brand.100"
            >
              <Stack gap={4}>
                <Text fontWeight="bold" color="brand.700">
                  What you get
                </Text>
                {FEATURES.slice(0, 3).map((f) => (
                  <Stack
                    key={f.title}
                    direction="row"
                    gap={3}
                    align="flex-start"
                  >
                    <Box color="brand.500" mt={1}>
                      <f.icon size={22} />
                    </Box>
                    <Box>
                      <Text fontWeight="semibold">{f.title}</Text>
                      <Text fontSize="sm" color="gray.600">
                        {f.description}
                      </Text>
                    </Box>
                  </Stack>
                ))}
              </Stack>
            </Box>
          </Grid>
        </Container>
      </Box>

      <Container maxW="container.xl" py={{ base: 14, md: 20 }} id="features">
        <Stack gap={4} textAlign="center" mb={12}>
          <Heading size="2xl">Platform capabilities</Heading>
          <Text color="gray.600" maxW="2xl" mx="auto">
            Syncdatesy bundles the customer UI, API adapter, migration services,
            and Kubernetes tooling in one monorepo—so product and ops stay
            aligned.
          </Text>
        </Stack>
        <SimpleGrid columns={{ base: 1, md: 2 }} gap={6}>
          {FEATURES.map((f) => (
            <Box
              key={f.title}
              p={6}
              borderRadius="xl"
              borderWidth="1px"
              borderColor="gray.100"
              bg="white"
              _hover={{ borderColor: "brand.200", shadow: "md" }}
              transition="all 0.2s"
            >
              <Box color="brand.500" mb={3}>
                <f.icon size={28} />
              </Box>
              <Heading size="md" mb={2}>
                {f.title}
              </Heading>
              <Text color="gray.600" fontSize="sm">
                {f.description}
              </Text>
            </Box>
          ))}
        </SimpleGrid>
      </Container>

      <Box id="about" bg="gray.50" py={{ base: 14, md: 20 }}>
        <Container maxW="container.xl">
          <Stack gap={4} textAlign="center" mb={10}>
            <Heading size="2xl">How it works</Heading>
            <Text color="gray.600">
              Three steps from connection to continuous sync
            </Text>
          </Stack>
          <SimpleGrid columns={{ base: 1, md: 3 }} gap={6}>
            {STEPS.map((s) => (
              <Box
                key={s.step}
                bg="white"
                p={6}
                borderRadius="xl"
                borderWidth="1px"
                borderColor="gray.100"
              >
                <Text fontSize="4xl" fontWeight="bold" color="brand.200" mb={2}>
                  {s.step}
                </Text>
                <Heading size="md" mb={2}>
                  {s.title}
                </Heading>
                <Text fontSize="sm" color="gray.600">
                  {s.body}
                </Text>
              </Box>
            ))}
          </SimpleGrid>
        </Container>
      </Box>

      <Box id="contact" py={{ base: 14, md: 16 }}>
        <Container maxW="container.lg">
          <Stack
            gap={4}
            textAlign="center"
            p={{ base: 8, md: 12 }}
            borderRadius="2xl"
            bgGradient="linear(to-r, brand.600, cyan.500)"
            color="white"
          >
            <Heading size="xl">Ready to explore Syncdatesy?</Heading>
            <Text opacity={0.95}>
              Sign in with your organization account or request trial access for
              your team.
            </Text>
            <Stack
              direction={{ base: "column", sm: "row" }}
              gap={3}
              justify="center"
            >
              <Button
                size="lg"
                bg="white"
                color="brand.800"
                _hover={{ bg: "gray.100" }}
                onClick={() =>
                  navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)
                }
              >
                Sign in
              </Button>
              <Button
                size="lg"
                variant="outline"
                borderColor="whiteAlpha.700"
                color="white"
                _hover={{ bg: "whiteAlpha.200" }}
                onClick={() =>
                  navigate(`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`)
                }
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
