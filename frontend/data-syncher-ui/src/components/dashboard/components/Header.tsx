import { useState } from "react";

import {
  Avatar,
  Box,
  Flex,
  GridItem,
  Menu,
  Portal,
  useBreakpointValue,
} from "@chakra-ui/react";

import { GoSidebarCollapse, GoSidebarExpand } from "react-icons/go";
import { RxHamburgerMenu } from "react-icons/rx";

import { useNavigate } from "react-router";

import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";

import SidebarMobile from "./Sidebar/SidebarMobile";

const Header = ({
  sidebarWidth,
  onSidebarToggle,
  isSidebarCollapsed,
}: {
  sidebarWidth: string;
  onSidebarToggle: () => void;
  isSidebarCollapsed: boolean;
}) => {
  const sidebarDisplay = useBreakpointValue({ base: "none", md: "block" });
  const {
    logout,
    authState: { user },
  } = useAuth();
  const [showSidebar, setShowSidebar] = useState(false);
  const navigate = useNavigate();

  return (
    <GridItem
      area="header"
      color="white"
      backgroundColor="white"
      position="fixed"
      top={0}
      left={sidebarDisplay === "block" ? sidebarWidth : "0"}
      right={0}
      height="60px"
      zIndex="999"
      paddingInline={6}
      display="flex"
      alignItems="center"
      boxShadow={"0 2px 4px rgba(0, 0, 0, 0.1)"}
    >
      {showSidebar && (
        <SidebarMobile
          showSidebar={showSidebar}
          setShowSidebar={setShowSidebar}
        />
      )}
      <Flex
        display={{ base: "none", md: "flex" }}
        alignItems="center"
        justifyContent="space-between"
        cursor="pointer"
        onClick={() => onSidebarToggle()}
        borderRadius="md"
        _hover={{
          bg: "gray.100",
        }}
        transition="all 0.2s ease-in-out"
      >
        <Box
          position="relative"
          display="flex"
          alignItems="center"
          justifyContent="center"
        >
          {/* Collapse Icon */}
          <Box
            position="absolute"
            opacity={isSidebarCollapsed ? 0 : 1}
            transform={
              isSidebarCollapsed
                ? "rotate(180deg) scale(0.8)"
                : "rotate(0deg) scale(1)"
            }
            transition="all 0.3s ease-in-out"
          >
            <GoSidebarExpand size={24} color="gray" />
          </Box>
          {/* Expand Icon */}
          <Box
            position="absolute"
            opacity={isSidebarCollapsed ? 1 : 0}
            transform={
              isSidebarCollapsed
                ? "rotate(0deg) scale(1)"
                : "rotate(-180deg) scale(0.8)"
            }
            transition="all 0.3s ease-in-out"
          >
            <GoSidebarCollapse size={24} color="gray" />
          </Box>
        </Box>
      </Flex>
      <Flex
        display={{ base: "flex", md: "none" }}
        alignItems="center"
        justifyContent="space-between"
        cursor="pointer"
        onClick={() => setShowSidebar(true)}
      >
        <RxHamburgerMenu size={24} color="gray" />
      </Flex>
      <Flex
        marginLeft="auto"
        justifyContent="space-between"
        alignItems={"center"}
      >
        <Menu.Root positioning={{ placement: "bottom-end" }}>
          <Menu.Trigger rounded="full">
            <Avatar.Root colorPalette="blue" cursor="pointer">
              <Avatar.Fallback name={user?.first_name} />
            </Avatar.Root>
          </Menu.Trigger>
          <Portal>
            <Menu.Positioner>
              <Menu.Content>
                <Menu.Item
                  value="my-profile"
                  cursor="pointer"
                  onClick={() => {
                    navigate(
                      `${ClientRoutes.USER_SETTINGS.ROOT}/${ClientRoutes.USER_SETTINGS.PROFILE}`,
                    );
                  }}
                >
                  My Profile
                </Menu.Item>
                <Menu.Item
                  value="logout"
                  cursor="pointer"
                  onClick={() => {
                    logout();
                  }}
                >
                  Logout
                </Menu.Item>
              </Menu.Content>
            </Menu.Positioner>
          </Portal>
        </Menu.Root>
      </Flex>
    </GridItem>
  );
};
export default Header;
