import { ReactNode } from "react";

import { Breadcrumb, Button, Flex, Text } from "@chakra-ui/react";

import { FaPlus } from "react-icons/fa6";

import { useNavigate } from "react-router";

interface PageHeaderProps {
  breadcrumbs: { label: string; route?: string; onClick?: () => void }[];
  buttonLabel?: string;
  onCreateClick?: () => void;
  children?: ReactNode;
  title?: ReactNode;
  subtitle?: string;
  titleBold?: boolean;
  rightElement?: ReactNode;
}

const PageHeader = ({
  breadcrumbs,
  buttonLabel,
  onCreateClick,
  children,
  title,
  subtitle,
  titleBold = true,
  rightElement,
}: PageHeaderProps) => {
  const navigate = useNavigate();

  const handleBreadcrumbClick = (route?: string) => {
    if (route) {
      navigate(route);
    }
  };

  return (
    <Flex justifyContent="space-between" align="center">
      <Flex direction="column" gap={1}>
        <Breadcrumb.Root size="lg">
          <Breadcrumb.List flexWrap={"wrap"} gap={2}>
            {breadcrumbs.map(({ route, label, onClick }, idx) => (
              <Breadcrumb.Item
                key={idx}
                cursor={route || onClick ? "pointer" : "default"}
              >
                <Breadcrumb.Link
                  onClick={() => {
                    if (onClick) onClick();
                    else handleBreadcrumbClick(route);
                  }}
                >
                  {label}
                </Breadcrumb.Link>
                <Breadcrumb.Separator as="span" />
              </Breadcrumb.Item>
            ))}
          </Breadcrumb.List>
        </Breadcrumb.Root>
        <Flex flexDirection={"column"} gap={0.2}>
          {title && (
            <Text fontSize="2xl" fontWeight={titleBold ? "bold" : "normal"}>
              {title}
            </Text>
          )}
          {subtitle && <Text fontSize="sm">{subtitle}</Text>}
          {children}
        </Flex>
      </Flex>
      <Flex gap={4} align="center">
        {rightElement}
        {buttonLabel && (
          <Button colorPalette="brand" onClick={onCreateClick}>
            <FaPlus />
            {buttonLabel}
          </Button>
        )}
      </Flex>
    </Flex>
  );
};

export default PageHeader;
