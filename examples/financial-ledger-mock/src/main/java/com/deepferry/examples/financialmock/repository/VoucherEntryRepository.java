package com.deepferry.examples.financialmock.repository;

import com.deepferry.examples.financialmock.entity.VoucherEntry;
import org.springframework.data.jpa.repository.JpaRepository;

public interface VoucherEntryRepository extends JpaRepository<VoucherEntry, Long> {
}
