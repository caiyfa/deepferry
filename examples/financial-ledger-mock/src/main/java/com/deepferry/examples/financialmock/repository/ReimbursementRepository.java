package com.deepferry.examples.financialmock.repository;

import com.deepferry.examples.financialmock.entity.Reimbursement;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.List;

public interface ReimbursementRepository extends JpaRepository<Reimbursement, Long> {

    List<Reimbursement> findByStatus(String status);

    List<Reimbursement> findByCategory(String category);

    List<Reimbursement> findByStatusAndCategory(String status, String category);

    @Query("SELECT r FROM Reimbursement r JOIN FETCH r.employee e WHERE e.department = :department")
    List<Reimbursement> findByEmployeeDepartment(@Param("department") String department);
}
