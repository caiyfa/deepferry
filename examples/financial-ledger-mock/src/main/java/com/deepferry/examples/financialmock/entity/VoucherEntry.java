package com.deepferry.examples.financialmock.entity;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import java.math.BigDecimal;

@Entity
@Table(name = "voucher_entry")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VoucherEntry {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "voucher_id")
    @JsonIgnore
    private Voucher voucher;

    @Column(name = "line_no")
    private Integer lineNo;

    @Column(name = "account_code", length = 20)
    private String accountCode;

    @Column(name = "account_name", length = 50)
    private String accountName;

    @Column(precision = 12, scale = 2)
    private BigDecimal debit;

    @Column(precision = 12, scale = 2)
    private BigDecimal credit;

    @Column(length = 100)
    private String auxiliary;
}
