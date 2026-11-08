export const TableData = [
  {
    records: "5,00,000",
    units: "<500",
    price: "-",
    lowAmount1: "140",
    lowAmount2: "-",
  },
  {
    records: "500K to 3M",
    units: ">500 - <3000",
    price: "0.47",
    lowAmount1: "235",
    lowAmount2: "1,410",
  },
  {
    records: "500K to 3M",
    units: ">3000 - <6000M",
    price: "0.42",
    lowAmount1: "1,260",
    lowAmount2: "2,250",
  },
  {
    records: "6M to 10M",
    units: ">3000 - 10000M",
    price: "0.40",
    lowAmount1: "2,400",
    lowAmount2: "4,000",
  },
  {
    records: ">10M",
    units: "-",
    price: "0.37",
    lowAmount1: "3,700",
    lowAmount2: "-",
  },
];

export type TableRow = {
  records: string;
  units: string;
  price: string;
  lowAmount1: string;
  lowAmount2: string;
};

export const CardData = [
  {
    id: 1,
    name: "Trial",
    description: `As an introductory offering for prospective customers, we can replicate and update up to 10 tables per connection for a period of two weeks. 
      This includes both initial and delta loads, with a focus on ease of use and quick setup.`,
    bulletPoints: [
      "Single user access",
      "Maximum of 2 million records per table",
      "1 Hour data syncs",
    ],
  },
  {
    id: 2,
    name: "Basic",
    description: `Full platform capabilities for customer aiming to automate their data movement.`,
    bulletPoints: [
      "Role based connection access",
      "Both ETL & Reverse ETL access",
      "1 Hour data syncs",
      "Pay-as-you-go or contractual pricing selection",
    ],
  },
  {
    id: 3,
    name: "Corporate",
    description: `Custom solutions for large organizations.`,
    bulletPoints: [
      "Role based connection access",
      "Both ETL & Reverse ETL access",
      "15 minutes data syncs based on the connection capability",
      "20% reduction in pricing",
    ],
  },
];

export const Description = `DataSyncher offers both pay-as-you-go and contractual pricing options.
          Contractual pricing provides a 20% reduction in annual charges.
          Additionally, the higher the data transfer volume, the lower the cost
          per unit.`;
