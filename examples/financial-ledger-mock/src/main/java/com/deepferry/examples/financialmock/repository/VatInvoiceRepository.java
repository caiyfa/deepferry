package com.deepferry.examples.financialmock.repository;

import com.deepferry.examples.financialmock.entity.VatInvoice;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface VatInvoiceRepository extends JpaRepository<VatInvoice, Long> {

    List<VatInvoice> findByInvoiceType(String invoiceType);
}
