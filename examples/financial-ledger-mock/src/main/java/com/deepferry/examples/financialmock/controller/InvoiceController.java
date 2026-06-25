package com.deepferry.examples.financialmock.controller;

import com.deepferry.examples.financialmock.common.ApiResponse;
import com.deepferry.examples.financialmock.entity.VatInvoice;
import com.deepferry.examples.financialmock.repository.VatInvoiceRepository;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/v1/invoices")
public class InvoiceController {

    private final VatInvoiceRepository invoiceRepository;

    public InvoiceController(VatInvoiceRepository invoiceRepository) {
        this.invoiceRepository = invoiceRepository;
    }

    @GetMapping
    public ApiResponse<InvoiceDto> listInvoices(@RequestParam(required = false) String invoiceType) {
        List<VatInvoice> invoices;
        if (invoiceType != null && !invoiceType.isEmpty()) {
            invoices = invoiceRepository.findByInvoiceType(invoiceType);
        } else {
            invoices = invoiceRepository.findAll();
        }
        List<InvoiceDto> dtos = invoices.stream()
                .map(InvoiceDto::from)
                .collect(Collectors.toList());
        return ApiResponse.of(dtos);
    }

    public record InvoiceDto(
            Long id,
            String invoiceNo,
            String invoiceCode,
            String invoiceType,
            Seller seller,
            Buyer buyer,
            BigDecimal amount,
            BigDecimal taxRate,
            BigDecimal taxAmount,
            BigDecimal totalAmount,
            LocalDate issueDate) {

        public static InvoiceDto from(VatInvoice inv) {
            return new InvoiceDto(
                    inv.getId(),
                    inv.getInvoiceNo(),
                    inv.getInvoiceCode(),
                    inv.getInvoiceType(),
                    new Seller(inv.getSellerName(), inv.getSellerTaxNo(),
                            inv.getSellerAddress(), inv.getSellerPhone()),
                    new Buyer(inv.getBuyerName(), inv.getBuyerTaxNo()),
                    inv.getAmount(),
                    inv.getTaxRate(),
                    inv.getTaxAmount(),
                    inv.getTotalAmount(),
                    inv.getIssueDate());
        }

        public record Seller(String name, String taxNo, String address, String phone) {}
        public record Buyer(String name, String taxNo) {}
    }
}
