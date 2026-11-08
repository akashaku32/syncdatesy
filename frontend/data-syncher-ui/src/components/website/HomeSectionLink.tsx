import { Link as ChakraLink } from "@chakra-ui/react";

import { Link, useLocation, useNavigate } from "react-router";

import ClientRoutes from "@/constants/client-routes";

type HomeSectionLinkProps = {
  sectionId: string;
  children: React.ReactNode;
};

export default function HomeSectionLink({
  sectionId,
  children,
}: HomeSectionLinkProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const scrollToSection = () => {
    document.getElementById(sectionId)?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  };

  return (
    <ChakraLink
      asChild
      color="gray.700"
      fontWeight="medium"
      _hover={{ color: "brand.600", textDecoration: "none" }}
    >
      <Link
        to={`/#${sectionId}`}
        onClick={(event) => {
          event.preventDefault();
          if (location.pathname !== ClientRoutes.HOME) {
            navigate(ClientRoutes.HOME, { state: { scrollTo: sectionId } });
            return;
          }
          scrollToSection();
          window.history.replaceState(null, "", `/#${sectionId}`);
        }}
      >
        {children}
      </Link>
    </ChakraLink>
  );
}
