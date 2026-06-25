package com.deepferry.examples.financialmock.controller;

import com.deepferry.examples.financialmock.common.ApiResponse;
import com.deepferry.examples.financialmock.entity.Reimbursement;
import com.deepferry.examples.financialmock.repository.ReimbursementRepository;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

@RestController
@RequestMapping("/api/v1/reimbursements")
@Transactional(readOnly = true)
public class ReimbursementController {

    private final ReimbursementRepository reimbursementRepository;

    public ReimbursementController(ReimbursementRepository reimbursementRepository) {
        this.reimbursementRepository = reimbursementRepository;
    }

    @GetMapping
    public ApiResponse<ReimbDto> listReimbursements(
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String department) {

        List<Reimbursement> reimbursements;

        if (department != null && !department.isEmpty()) {
            reimbursements = reimbursementRepository.findByEmployeeDepartment(department);
        } else if (status != null && !status.isEmpty() && category != null && !category.isEmpty()) {
            reimbursements = reimbursementRepository.findByStatusAndCategory(status, category);
        } else if (status != null && !status.isEmpty()) {
            reimbursements = reimbursementRepository.findByStatus(status);
        } else if (category != null && !category.isEmpty()) {
            reimbursements = reimbursementRepository.findByCategory(category);
        } else {
            reimbursements = reimbursementRepository.findAll();
        }

        List<ReimbDto> dtos = new ArrayList<>();
        for (Reimbursement r : reimbursements) {
            dtos.add(ReimbDto.from(r));
        }
        return ApiResponse.of(dtos);
    }

    public record ReimbDto(
            Long id,
            String reimbNo,
            EmployeeRef employee,
            InvoiceRef invoice,
            String category,
            BigDecimal amount,
            String description,
            String status,
            LocalDate applyDate,
            String approvedBy) {

        public static ReimbDto from(Reimbursement r) {
            EmployeeRef empRef = null;
            if (r.getEmployee() != null) {
                var e = r.getEmployee();
                empRef = new EmployeeRef(e.getId(), e.getEmpNo(), e.getName(), e.getDepartment());
            }
            InvoiceRef invRef = null;
            if (r.getInvoice() != null) {
                var i = r.getInvoice();
                invRef = new InvoiceRef(i.getId(), i.getInvoiceNo(), i.getTotalAmount());
            }
            return new ReimbDto(
                    r.getId(), r.getReimbNo(), empRef, invRef,
                    r.getCategory(), r.getAmount(), r.getDescription(),
                    r.getStatus(), r.getApplyDate(), r.getApprovedBy());
        }

        public record EmployeeRef(Long id, String empNo, String name, String department) {}
        public record InvoiceRef(Long id, String invoiceNo, BigDecimal totalAmount) {}
    }
}
