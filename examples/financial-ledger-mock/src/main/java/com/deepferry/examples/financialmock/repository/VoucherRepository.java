package com.deepferry.examples.financialmock.repository;

import com.deepferry.examples.financialmock.entity.Voucher;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.List;

public interface VoucherRepository extends JpaRepository<Voucher, Long> {

    List<Voucher> findByStatus(String status);

    List<Voucher> findByPeriod(String period);

    @Query("SELECT v FROM Voucher v LEFT JOIN FETCH v.entries LEFT JOIN FETCH v.reimb WHERE v.id = :id")
    Voucher findByIdWithEntries(@Param("id") Long id);

    @Query("SELECT v FROM Voucher v LEFT JOIN FETCH v.reimb ORDER BY v.voucherNo")
    List<Voucher> findAllWithReimb();
}
