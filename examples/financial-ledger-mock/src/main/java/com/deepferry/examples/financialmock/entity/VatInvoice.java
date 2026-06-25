package com.deepferry.examples.financialmock.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import java.math.BigDecimal;
import java.time.LocalDate;

@Entity
@Table(name = "vat_invoice")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VatInvoice {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "invoice_no", unique = true, length = 30, nullable = false)
    private String invoiceNo;

    @Column(name = "invoice_code", length = 30)
    private String invoiceCode;

    @Column(name = "invoice_type", length = 20)
    private String invoiceType;

    @Column(name = "seller_name", length = 200)
    private String sellerName;

    @Column(name = "seller_tax_no", length = 50)
    private String sellerTaxNo;

    @Column(name = "seller_address", length = 300)
    private String sellerAddress;

    @Column(name = "seller_phone", length = 30)
    private String sellerPhone;

    @Column(name = "buyer_name", length = 200)
    private String buyerName;

    @Column(name = "buyer_tax_no", length = 50)
    private String buyerTaxNo;

    @Column(precision = 12, scale = 2)
    private BigDecimal amount;

    @Column(name = "tax_rate", precision = 5, scale = 4)
    private BigDecimal taxRate;

    @Column(name = "tax_amount", precision = 12, scale = 2)
    private BigDecimal taxAmount;

    @Column(name = "total_amount", precision = 12, scale = 2)
    private BigDecimal totalAmount;

    @Column(name = "issue_date")
    private LocalDate issueDate;
}
