package com.deepferry.examples.financialmock.entity;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OrderBy;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "voucher")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class Voucher {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "voucher_no", unique = true, length = 30, nullable = false)
    private String voucherNo;

    @Column(name = "`period`", length = 10)
    private String period;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "reimb_id")
    private Reimbursement reimb;

    @Column(length = 200)
    private String summary;

    @Column(name = "total_debit", precision = 12, scale = 2)
    private BigDecimal totalDebit;

    @Column(name = "total_credit", precision = 12, scale = 2)
    private BigDecimal totalCredit;

    @Column(name = "posted_by", length = 50)
    private String postedBy;

    @Column(name = "posted_date")
    private LocalDate postedDate;

    @Column(length = 20)
    private String status;

    @OneToMany(mappedBy = "voucher", cascade = CascadeType.ALL, fetch = FetchType.LAZY)
    @OrderBy("lineNo ASC")
    @Builder.Default
    private List<VoucherEntry> entries = new ArrayList<>();
}
