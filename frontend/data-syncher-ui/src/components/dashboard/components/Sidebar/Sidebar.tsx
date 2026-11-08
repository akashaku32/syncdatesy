import {
  Box,
  Flex,
  GridItem,
  Image,
  Text,
  useBreakpointValue,
} from "@chakra-ui/react";

import { BiSupport } from "react-icons/bi";
import { FaUsers } from "react-icons/fa6";
import { MdCategory, MdOutlineArrowRightAlt, MdWrapText } from "react-icons/md";
import { PiFlowArrowBold } from "react-icons/pi";

import { useLocation, useNavigate } from "react-router";

import CustomerIcon from "@/assets/images/customer-icon.svg";
import Logo from "@/assets/images/logo.svg";
import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";
import usePermissions from "@/hooks/usePermissions";

import SidebarAccordion from "./Accordion";
import MenuItem from "./MenuItem";

const Sidebar = ({
  sidebarWidth,
  isDrawer,
  onMenuItemClick,
}: {
  sidebarWidth: string;
  isDrawer: boolean;
  onMenuItemClick?: () => void;
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { can } = usePermissions();
  const breakpointDisplay = useBreakpointValue({ base: "none", md: "block" });
  const sidebarDisplay = isDrawer ? "block" : breakpointDisplay;
  const isActive = (path: string, exact = false) => {
    if (exact) return location.pathname === path;
    if (path.includes("/")) return location.pathname.includes(path);
    const segments = location.pathname.split("/");
    return segments.some((seg) => seg === path);
  };
  const {
    authState: { user },
  } = useAuth();

  const NavLinks = [
    {
      label: "Connectors",
      icon: <MdOutlineArrowRightAlt size={24} />,
      path: ClientRoutes.CONNECTORS.ROOT,
      isAccordion: false,
      visible: can("can_view_connectors"),
    },
    {
      label: "Destination",
      icon: <MdWrapText size={24} />,
      path: ClientRoutes.DESTINATION.ROOT,
      isAccordion: false,
      visible: can("can_view_destinations"),
    },
    {
      label: "Scheduling",
      icon: <PiFlowArrowBold size={20} />,
      path: ClientRoutes.SCHEDULING,
      isAccordion: false,
      visible: can("can_view_connectors"),
    },
    {
      label: "User Settings",
      icon: <FaUsers size={24} />,
      path: ClientRoutes.USER_SETTINGS.ROOT,
      isAccordion: true,
      visible: true,
    },
    {
      label: "Plans",
      icon: <MdCategory size={24} />,
      path: ClientRoutes.PLANS,
      isAccordion: false,
      visible: can("can_access_billing"),
    },
    {
      label: "Support",
      icon: <BiSupport size={24} />,
      path: ClientRoutes.SUPPORT,
      isAccordion: false,
      visible: true,
    },
  ].filter((link) => link.visible);

  return (
    <GridItem
      paddingBlock={4}
      area="sidebar"
      bg="#2A2D3E"
      display={sidebarDisplay}
      position="fixed"
      top={0}
      left={0}
      width={sidebarWidth}
      height="100vh"
      overflowY="auto"
      zIndex="1000"
      color="white"
      //transition={"width 0.2s ease-in-out"}
    >
      <Flex justifyContent="flex-start" alignItems="center" gap={2} p={2}>
        <Image
          src={Logo}
          alt="Logo"
          width={{ base: "40px", md: "30px" }}
          height={{ base: "40px", md: "30px" }}
          cursor="pointer"
          transition="transform 0.2s"
          _hover={{ transform: "scale(1.05)" }}
          style={{ fill: "white" }}
          onClick={() => navigate(ClientRoutes.CONNECTORS.ROOT)}
        />
        <Text fontSize="2xl" fontWeight="semibold">
          Datasyncher
        </Text>
      </Flex>
      <Flex marginBlock={8}>
        <Flex
          borderLeft="3px solid"
          borderColor="brand.accentOrange"
          alignItems="center"
          gap={4}
          h="50px"
        >
          <Image
            src={CustomerIcon}
            alt="Customer Icon"
            width="28px"
            height="28px"
            ml={2}
          />
          <Box>
            <Text fontSize="xl" fontWeight="semibold">
              {user?.company?.cmp_name}
            </Text>
          </Box>
        </Flex>
      </Flex>
      <Flex flexDirection={"column"} gap={2}>
        {NavLinks.map((props) => {
          const { label, icon, path, isAccordion } = props;
          const active = path ? isActive(path) : false;

          if (isAccordion) {
            return (
              <SidebarAccordion
                key={label}
                isActive={isActive}
                onMenuItemClick={onMenuItemClick}
              />
            );
          }

          return (
            <MenuItem
              key={label}
              label={label}
              icon={icon}
              path={path}
              active={active}
              onMenuItemClick={onMenuItemClick}
            />
          );
        })}
      </Flex>
    </GridItem>
  );
};

export default Sidebar;
