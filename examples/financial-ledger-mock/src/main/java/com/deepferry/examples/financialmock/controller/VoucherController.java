package com.deepferry.examples.financialmock.controller;

import com.deepferry.examples.financialmock.common.ApiResponse;
import com.deepferry.examples.financialmock.entity.Voucher;
import com.deepferry.examples.financialmock.entity.VoucherEntry;
import com.deepferry.examples.financialmock.repository.VoucherRepository;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.NoSuchElementException;

@RestController
@RequestMapping("/api/v1/vouchers")
@Transactional(readOnly = true)
public class VoucherController {

    private final VoucherRepository voucherRepository;

    public VoucherController(VoucherRepository voucherRepository) {
        this.voucherRepository = voucherRepository;
    }

    @GetMapping
    public ApiResponse<VoucherListDto> listVouchers(
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String period) {

        List<Voucher> vouchers;
        if (status != null && !status.isEmpty()) {
            vouchers = voucherRepository.findByStatus(status);
        } else if (period != null && !period.isEmpty()) {
            vouchers = voucherRepository.findByPeriod(period);
        } else {
            vouchers = voucherRepository.findAllWithReimb();
        }

        // For list endpoint, include entries (lazy-loaded)
        List<VoucherListDto> dtos = new ArrayList<>();
        for (Voucher v : vouchers) {
            dtos.add(VoucherListDto.from(v));
        }
        return ApiResponse.of(dtos);
    }

    @GetMapping("/{id}")
    public VoucherDetailDto getVoucher(@PathVariable Long id) {
        Voucher v = voucherRepository.findByIdWithEntries(id);
        if (v == null) {
            throw new NoSuchElementException("Voucher not found: " + id);
        }
        return VoucherDetailDto.from(v);
    }

    public record ReimbRef(Long id, String reimbNo, BigDecimal amount, String employeeName) {}

    public record EntryDto(
            Integer lineNo,
            String accountCode,
            String accountName,
            BigDecimal debit,
            BigDecimal credit,
            String auxiliary) {

        public static EntryDto from(VoucherEntry e) {
            return new EntryDto(e.getLineNo(), e.getAccountCode(),
                    e.getAccountName(), e.getDebit(), e.getCredit(), e.getAuxiliary());
        }
    }

    public record VoucherListDto(
            Long id,
            String voucherNo,
            String period,
            ReimbRef reimb,
            String summary,
            BigDecimal totalDebit,
            BigDecimal totalCredit,
            List<EntryDto> entries,
            String postedBy,
            LocalDate postedDate,
            String status) {

        public static VoucherListDto from(Voucher v) {
            ReimbRef reimbRef = null;
            if (v.getReimb() != null) {
                var r = v.getReimb();
                String empName = r.getEmployee() != null ? r.getEmployee().getName() : null;
                reimbRef = new ReimbRef(r.getId(), r.getReimbNo(), r.getAmount(), empName);
            }
            List<EntryDto> entryDtos = new ArrayList<>();
            if (v.getEntries() != null) {
                for (VoucherEntry e : v.getEntries()) {
                    entryDtos.add(EntryDto.from(e));
                }
            }
            return new VoucherListDto(
                    v.getId(), v.getVoucherNo(), v.getPeriod(), reimbRef,
                    v.getSummary(), v.getTotalDebit(), v.getTotalCredit(),
                    entryDtos, v.getPostedBy(), v.getPostedDate(), v.getStatus());
        }
    }

    public record VoucherDetailDto(
            Long id,
            String voucherNo,
            String period,
            ReimbRef reimb,
            String summary,
            BigDecimal totalDebit,
            BigDecimal totalCredit,
            List<EntryDto> entries,
            String postedBy,
            LocalDate postedDate,
            String status) {

        public static VoucherDetailDto from(Voucher v) {
            ReimbRef reimbRef = null;
            if (v.getReimb() != null) {
                var r = v.getReimb();
                String empName = r.getEmployee() != null ? r.getEmployee().getName() : null;
                reimbRef = new ReimbRef(r.getId(), r.getReimbNo(), r.getAmount(), empName);
            }
            List<EntryDto> entryDtos = new ArrayList<>();
            if (v.getEntries() != null) {
                for (VoucherEntry e : v.getEntries()) {
                    entryDtos.add(EntryDto.from(e));
                }
            }
            return new VoucherDetailDto(
                    v.getId(), v.getVoucherNo(), v.getPeriod(), reimbRef,
                    v.getSummary(), v.getTotalDebit(), v.getTotalCredit(),
                    entryDtos, v.getPostedBy(), v.getPostedDate(), v.getStatus());
        }
    }
}
